import hashlib, json
from src.constants.enums import BlindStatus, CandidateStatus


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}


def review_seal_dict(review, user):
    """封存信息序列化。

    揭榜前：仅负责人(admin)可见验收集逐点参考标签与分组明细；
    分析员/观察员只能看到两组计数与指纹，无法反推哪些点属于验收集。
    揭榜后：整组公开分组与参考标签。
    """
    seal = review.seal or {}
    revealed = review.status == BlindStatus.revealed.value
    base = {
        'dataset_checksum': seal.get('dataset_checksum'),
        'dataset_rows': seal.get('dataset_rows'),
        'seed': seal.get('seed'),
        'holdout_ratio': seal.get('holdout_ratio'),
        'reference_params': seal.get('reference_params'),
        'group_fingerprint': seal.get('group_fingerprint'),
        'tuning_count': seal.get('tuning_count'),
        'holdout_count': seal.get('holdout_count'),
        'frozen_at': seal.get('frozen_at'),
    }
    if revealed or user.role == 'admin':
        # 负责人在揭榜前可查看逐点登记（用于封存验收），但分析员不可见
        base['groups'] = seal.get('groups')
    else:
        base['groups_hidden'] = True
    return base


def candidate_dict(cand, review, user):
    """候选序列化。

    揭榜前：验收标签与逐点结果对分析员/观察员隐藏（直接查询、下载均不泄露）；
    仅返回冻结参数与状态。负责人在揭榜前同样看不到评估结果（评估尚未发生）。
    揭榜后：整组公开全部候选的指标与逐点结果。
    """
    revealed = review.status == BlindStatus.revealed.value
    data = {
        'id': cand.id, 'review_id': cand.review_id, 'alias': cand.alias,
        'params': cand.params, 'status': cand.status, 'error': cand.error,
        'created_at': cand.created_at, 'finished_at': cand.finished_at,
    }
    if revealed:
        data['result'] = cand.result
        data['tuning_summary'] = cand.tuning_summary
    else:
        # 半组/未揭榜：逐点结果与指标一律不公开，只暴露是否已算完的状态
        data['result_hidden'] = True
        if cand.status == CandidateStatus.done.value:
            data['evaluated'] = True
    return data


def review_dict(review, user, candidates=None, creator_email=None):
    data = {
        'id': review.id, 'name': review.name, 'dataset_id': review.dataset_id,
        'status': review.status, 'eval_state': review.eval_state,
        'created_at': review.created_at, 'revealed_at': review.revealed_at,
        'created_by': review.created_by,
        'creator_email': creator_email,
        'seal': review_seal_dict(review, user),
    }
    if candidates is not None:
        data['candidates'] = [candidate_dict(c, review, user) for c in candidates]
    return data

