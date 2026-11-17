"""盲评服务：封存调参/验收分组，冻结候选参数，揭榜时独立评估。

口径与业务算法一致：沿里程一维 DBSCAN（公里×1000 转米），噪声标签 -1。
本模块只服务盲评流程，不改变原试验（jobs）任何行为。
"""
import time

import numpy as np
from fastapi import HTTPException
from sklearn.cluster import DBSCAN

from src.constants.enums import BlindStatus, CandidateStatus
from src.models import Dataset, BlindReview, BlindCandidate
from src.services.business import metadata
from src.utils.serialization import checksum

TUNING = 'tuning'
HOLDOUT = 'holdout'
NOISE = -1


# ---------- 纯函数核心（便于独立测试） ----------

def validate_params(params):
    """与业务口径一致的参数校验，返回规范化后的 eps/min_points。"""
    try:
        eps = float(params.get('eps_m', 100))
        raw_min = float(params.get('min_points', 5))
        minimum = int(raw_min)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('邻域半径与最少点数必须是有效数值。') from exc
    if not 20 <= eps <= 400 or minimum != raw_min or not 3 <= minimum <= 12:
        raise ValueError('半径须20~400米，最少点数须3~12的整数。')
    return eps, minimum


def validate_records(records):
    """复用业务失败态：负里程、非有限、等级越界、点数不足均拒绝。"""
    if len(records) < 10:
        raise ValueError('至少需要10个缺陷点才能建立盲评。')
    try:
        mileage = np.asarray([float(r['mileage_km']) for r in records])
        lateral = np.asarray([float(r['lateral_mm']) for r in records])
        severity = np.asarray([float(r['severity']) for r in records])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('里程、横向偏差与复核等级必须完整且为数值。') from exc
    if not np.isfinite(np.column_stack([mileage, lateral, severity])).all() or (mileage < 0).any():
        raise ValueError('里程须非负有限，横向偏差须有限。')
    if not np.isin(severity, [1, 2, 3, 4, 5]).all():
        raise ValueError('复核等级须为1~5整数。')
    return mileage


def point_labels(records, params):
    """对给定记录跑一维 DBSCAN，返回与 records 等长、等序的整数标签列表（噪声=-1）。"""
    eps, minimum = validate_params(params)
    mileage = np.asarray([float(r['mileage_km']) for r in records], dtype=float)
    labels = DBSCAN(eps=eps, min_samples=minimum).fit_predict((mileage * 1000).reshape(-1, 1))
    return [int(v) for v in labels]


def split_groups(records, holdout_ratio, seed):
    """按点登记参考分组：确定性随机划分，同一批次的点只属于一个集合，不跨两集。

    返回 [(defect_id, group)]，分组与记录顺序无关、可由 seed 复现。
    """
    if not 0.1 <= holdout_ratio <= 0.5:
        raise ValueError('验收集占比须在 10%~50% 之间。')
    ids = [r['defect_id'] for r in records]
    if len(set(ids)) != len(ids):
        raise ValueError('缺陷编号 defect_id 存在重复，无法逐点登记分组。')
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(records))
    holdout_n = max(2, int(round(len(records) * holdout_ratio)))
    holdout_n = min(holdout_n, len(records) - 2)  # 调参集至少保留2点
    holdout_idx = set(order[:holdout_n].tolist())
    return [(records[i]['defect_id'], HOLDOUT if i in holdout_idx else TUNING) for i in range(len(records))]


def evaluate_holdout(records, groups, params):
    """在封存验收集上独立评估候选参数。

    参考标签来自封存时以参考参数对“整批数据”计算的逐点标签；
    候选预测标签来自候选参数对“整批数据”的计算，再仅取验收集点对比，
    保证候选在调参集上学到的密度结构不会被割裂。
    返回逐点结果与汇总指标。
    """
    eps, minimum = validate_params(params)
    pred_all = point_labels(records, params)
    id_to_pred = {records[i]['defect_id']: pred_all[i] for i in range(len(records))}
    holdout_ids = [g[0] for g in groups if g[1] == HOLDOUT]
    id_to_ref = {g[0]: g[2] for g in groups}

    points, pair_errors, isolated_errors = [], 0, 0
    in_band_correct, in_band_total = 0, 0
    iso_correct, iso_total = 0, 0
    ref_in_band = []
    for defect_id in holdout_ids:
        ref = id_to_ref[defect_id]
        pred = id_to_pred[defect_id]
        ref_iso, pred_iso = ref == NOISE, pred == NOISE
        if ref_iso:
            iso_total += 1
            iso_correct += 1 if pred_iso else 0
            isolated_errors += 1 if not pred_iso else 0
        else:
            in_band_total += 1
            in_band_correct += 1 if not pred_iso else 0
            ref_in_band.append(defect_id)
        # 孤立点误判：参考为孤立、候选却并入某带
        isolated_wrong = ref_iso and not pred_iso
        points.append({
            'defect_id': defect_id,
            'reference_label': ref,
            'candidate_label': pred,
            'reference_isolated': ref_iso,
            'candidate_isolated': pred_iso,
            'isolated_misjudge': isolated_wrong,
        })

    # 带内点对：参考同属一条带的点对，候选是否仍判为同带（允许带编号不同）
    ref_band_of = {g[0]: g[2] for g in groups}
    pred_band_of = id_to_pred
    pair_total, pair_match = 0, 0
    for a_i in range(len(ref_in_band)):
        for b_i in range(a_i + 1, len(ref_in_band)):
            a, b = ref_in_band[a_i], ref_in_band[b_i]
            if ref_band_of[a] != ref_band_of[b]:
                continue  # 只统计参考带内点对
            pair_total += 1
            same_pred = (not pred_band_of[a] == NOISE and pred_band_of[a] == pred_band_of[b])
            pair_match += 1 if same_pred else 0
            if not same_pred:
                pair_errors += 1

    n = len(holdout_ids)
    return {
        'params': {'eps_m': eps, 'min_points': minimum},
        'holdout_points': n,
        'metrics': {
            'in_band_pair_total': pair_total,
            'in_band_pair_errors': pair_errors,
            'in_band_pair_accuracy': round(pair_match / pair_total, 4) if pair_total else None,
            'isolated_total': iso_total,
            'isolated_misjudge': isolated_errors,
            'isolated_accuracy': round(iso_correct / iso_total, 4) if iso_total else None,
            'in_band_recall': round(in_band_correct / in_band_total, 4) if in_band_total else None,
            'overall_accuracy': round(sum(1 for p in points if p['reference_isolated'] == p['candidate_isolated']) / n, 4) if n else None,
        },
        'points': points,
    }


# ---------- 封存与揭榜（带数据库会话） ----------

def create_review(db, data, user):
    dataset = db.get(Dataset, data.dataset_id)
    if not dataset:
        raise HTTPException(404, '数据批次不存在')
    records = dataset.records
    validate_records(records)
    # 参考参数即业务默认口径，封存后不可改
    ref_params = {p['key']: p['default'] for p in metadata().get('parameters', [])}
    validate_params(ref_params)
    seed = int(data.seed) if data.seed is not None else 1011
    raw_groups = split_groups(records, data.holdout_ratio, seed)
    # 参考标签：参考参数对整批数据计算，逐点登记
    ref_labels = point_labels(records, ref_params)
    id_to_label = {records[i]['defect_id']: ref_labels[i] for i in range(len(records))}
    groups = [[defect_id, group, id_to_label[defect_id]] for defect_id, group in raw_groups]
    tuning_n = sum(1 for g in groups if g[1] == TUNING)
    holdout_n = len(groups) - tuning_n
    seal = {
        'dataset_checksum': dataset.checksum,
        'dataset_rows': len(records),
        'seed': seed,
        'holdout_ratio': data.holdout_ratio,
        'reference_params': ref_params,
        'groups': groups,  # [defect_id, group, reference_label]
        'group_fingerprint': checksum(groups),
        'tuning_count': tuning_n,
        'holdout_count': holdout_n,
        'frozen_at': time.time(),
    }
    review = BlindReview(
        name=data.name.strip(), dataset_id=data.dataset_id, seal=seal,
        status=BlindStatus.sealed.value, eval_state='pending',
        created_by=user.id, created_at=time.time(),
    )
    db.add(review)
    db.flush()
    return review


def add_candidate(db, review_id, data, user):
    review = db.get(BlindReview, review_id)
    if not review:
        raise HTTPException(404, '盲评不存在')
    if review.status != BlindStatus.sealed.value:
        raise HTTPException(409, '揭榜已开始，拒绝换参、换样，不能再新增候选。')
    try:
        eps, minimum = validate_params(data.params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    params = {'eps_m': eps, 'min_points': minimum}
    cands = review_candidates(db, review_id)
    # 同一盲评内候选参数不得重复，避免重复计分
    if any(c.params == params for c in cands):
        raise HTTPException(409, '该候选参数已冻结在此盲评中，请勿重复登记。')
    cand = BlindCandidate(
        review_id=review_id, alias=(data.alias or f'候选{len(cands) + 1}').strip()[:80],
        params=params, status=CandidateStatus.frozen.value,
        created_by=user.id, created_at=time.time(),
    )
    db.add(cand)
    db.flush()
    return cand


def review_candidates(db, review_id):
    from sqlalchemy import select
    return list(db.scalars(select(BlindCandidate).where(BlindCandidate.review_id == review_id).order_by(BlindCandidate.id)))


def start_reveal(db, review_id, user):
    """发起揭榜：冻结态校验，把全部已冻结候选排入同次评估。整组公开、不接受半组。

    行锁保证并发揭榜只有一个请求能完成 sealed→revealing 转换，
    其余请求拿到 revealing 状态后被拒绝，沿用同次评估、不重复计分。
    """
    from sqlalchemy import select
    stmt = select(BlindReview).where(BlindReview.id == review_id)
    if db.bind.dialect.name != 'sqlite':
        stmt = stmt.with_for_update()
    review = db.scalar(stmt)
    if not review:
        raise HTTPException(404, '盲评不存在')
    if review.status == BlindStatus.revealed.value:
        raise HTTPException(409, '该盲评已揭榜，结果已整组公开。')
    if review.status == BlindStatus.revealing.value:
        raise HTTPException(409, '同次揭榜评估进行中，已沿用本次评估，请勿重复发起。')
    cands = review_candidates(db, review_id)
    frozen = [c for c in cands if c.status == CandidateStatus.frozen.value]
    if not frozen:
        raise HTTPException(422, '没有已冻结候选，无法揭榜。')
    review.status = BlindStatus.revealing.value
    review.eval_state = 'running'
    for c in frozen:
        c.status = CandidateStatus.queued.value
    db.flush()
    return review, [c.id for c in frozen]


def evaluate_candidate(db, candidate_id):
    """执行单个候选的验收集评估。幂等：已完成的不重复计分，中断后可恢复。"""
    cand = db.get(BlindCandidate, candidate_id)
    if not cand or cand.status == CandidateStatus.done.value:
        return cand
    if cand.status not in (CandidateStatus.queued.value, CandidateStatus.running.value):
        return cand
    cand.status = CandidateStatus.running.value
    db.commit()
    try:
        review = db.get(BlindReview, cand.review_id)
        dataset = db.get(Dataset, review.dataset_id)
        if dataset.checksum != review.seal['dataset_checksum']:
            raise RuntimeError('封存样本指纹与当前批次不一致，拒绝评估。')
        result = evaluate_holdout(dataset.records, review.seal['groups'], cand.params)
        cand.result = result
        cand.tuning_summary = _tuning_summary(dataset.records, review.seal['groups'], cand.params)
        cand.status = CandidateStatus.done.value
        cand.finished_at = time.time()
        cand.error = ''
    except Exception as exc:  # 单个候选失败不影响同组其他候选
        cand.status = CandidateStatus.failed.value
        cand.error = str(exc)[:990]
        cand.finished_at = time.time()
    db.commit()
    return cand


def _tuning_summary(records, groups, params):
    """调参集上的聚合摘要（不含验收集标签），仅记录候选口径信息。"""
    tuning_ids = {g[0] for g in groups if g[1] == TUNING}
    tuning_records = [r for r in records if r['defect_id'] in tuning_ids]
    labels = point_labels(records, params)
    id_to_label = {records[i]['defect_id']: labels[i] for i in range(len(records))}
    t_labels = [id_to_label[r['defect_id']] for r in tuning_records]
    bands = len({v for v in t_labels if v != NOISE})
    return {'tuning_points': len(tuning_records), 'tuning_bands': bands,
            'tuning_noise': sum(1 for v in t_labels if v == NOISE)}


def finalize_if_complete(db, review_id):
    """所有候选都有终态（done/failed）后整组公开；半组结果保持不公开。"""
    review = db.get(BlindReview, review_id)
    if not review or review.status != BlindStatus.revealing.value:
        return review
    cands = review_candidates(db, review_id)
    terminal = all(c.status in (CandidateStatus.done.value, CandidateStatus.failed.value) for c in cands)
    if terminal and any(c.status == CandidateStatus.done.value for c in cands):
        review.status = BlindStatus.revealed.value
        review.eval_state = 'done'
        review.revealed_at = time.time()
        db.commit()
    elif terminal:
        review.eval_state = 'failed'
        db.commit()
    return review
