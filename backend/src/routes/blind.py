from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from src.config.database import get_db
from src.constants.enums import BlindStatus
from src.models import BlindReview, BlindCandidate, User
from src.schemas.blind import BlindReviewInput, BlindCandidateInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import blind
from src.services.audit import record
from src.utils.serialization import review_dict, candidate_dict

router = APIRouter(prefix='/blind-reviews')


def _with_creator(db, review, user, cands=None):
    creator = db.get(User, review.created_by) if review.created_by else None
    return review_dict(review, user, cands, creator.email if creator else None)


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    reviews = list(db.scalars(select(BlindReview).order_by(BlindReview.id.desc())))
    out = []
    for r in reviews:
        cands = blind.review_candidates(db, r.id)
        item = _with_creator(db, r, u, cands)
        item['candidate_count'] = len(cands)
        out.append(item)
    return out


@router.post('', status_code=201)
def create(data: BlindReviewInput, db=Depends(get_db), u=Depends(can_write)):
    # 建盲评：分析员可发起封存，但封存验收集的参考标签对其隐藏
    try:
        r = blind.create_review(db, data, u)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    record(db, u.email, 'blind.create', {'id': r.id, 'holdout_ratio': data.holdout_ratio, 'seed': r.seal['seed']})
    db.commit()
    return _with_creator(db, db.get(BlindReview, r.id), u, [])


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    r = db.get(BlindReview, id)
    if not r:
        raise HTTPException(404, '盲评不存在')
    return _with_creator(db, r, u, blind.review_candidates(db, r.id))


@router.post('/{id}/candidates', status_code=201)
def add_candidate(id: int, data: BlindCandidateInput, db=Depends(get_db), u=Depends(can_write)):
    # 调参阶段登记冻结候选；揭榜开始后服务层直接拒绝
    c = blind.add_candidate(db, id, data, u)
    record(db, u.email, 'blind.candidate.freeze', {'id': c.id, 'review_id': id, 'params': c.params})
    db.commit()
    return candidate_dict(c, db.get(BlindReview, id), u)


@router.post('/{id}/reveal')
def reveal(id: int, db=Depends(get_db), u=Depends(admin_only)):
    # 揭榜仅负责人可发起；只评已冻结候选，开始后拒绝换参换样
    r, candidate_ids = blind.start_reveal(db, id, u)
    record(db, u.email, 'blind.reveal.start', {'id': id, 'candidates': candidate_ids})
    db.commit()
    # 同次评估内联执行：并发请求会因状态变为 revealing 而被服务层拒绝（幂等沿用）
    for cid in candidate_ids:
        blind.evaluate_candidate(db, cid)
    blind.finalize_if_complete(db, id)
    db.refresh(r)
    if r.status == 'revealed':
        record(db, u.email, 'blind.reveal.done', {'id': id})
        db.commit()
    return _with_creator(db, r, u, blind.review_candidates(db, id))


@router.post('/{id}/resume')
def resume(id: int, db=Depends(get_db), u=Depends(admin_only)):
    # 中断恢复：只重算排队/评估中的候选，已完成候选不重复计分
    r = db.get(BlindReview, id)
    if not r:
        raise HTTPException(404, '盲评不存在')
    if r.status != 'revealing':
        raise HTTPException(409, '该盲评不在揭榜评估中，无需恢复。')
    cands = blind.review_candidates(db, id)
    recovered = []
    for c in cands:
        if c.status in ('queued', 'running'):
            blind.evaluate_candidate(db, c.id)
            recovered.append(c.id)
    blind.finalize_if_complete(db, id)
    db.refresh(r)
    record(db, u.email, 'blind.reveal.resume', {'id': id, 'recovered': recovered})
    db.commit()
    return _with_creator(db, db.get(BlindReview, id), u, blind.review_candidates(db, id))


@router.get('/{id}/candidates/{cid}')
def candidate_detail(id: int, cid: int, db=Depends(get_db), u=Depends(current_user)):
    # 直接查询同样受脱敏约束：揭榜前不返回验收标签与逐点结果
    r = db.get(BlindReview, id)
    c = db.get(BlindCandidate, cid)
    if not r or not c or c.review_id != r.id:
        raise HTTPException(404, '候选不存在')
    return candidate_dict(c, r, u)


@router.get('/{id}/export')
def export(id: int, db=Depends(get_db), u=Depends(current_user)):
    """整组公开后才允许下载逐点验收结果；揭榜前任何角色直接请求均拒绝，不泄露标签。"""
    r = db.get(BlindReview, id)
    if not r:
        raise HTTPException(404, '盲评不存在')
    if r.status != BlindStatus.revealed.value:
        record(db, u.email, 'blind.export.blocked', {'id': id, 'status': r.status})
        db.commit()
        raise HTTPException(409, '盲评尚未整组揭榜，验收标签与逐点结果封存中，禁止下载。')
    payload = _with_creator(db, r, u, blind.review_candidates(db, id))
    record(db, u.email, 'blind.export', {'id': id})
    db.commit()
    return JSONResponse(payload, headers={'Content-Disposition': f'attachment; filename="blind-review-{id}-revealed.json"'})
