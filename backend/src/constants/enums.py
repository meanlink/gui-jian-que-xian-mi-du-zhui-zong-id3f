from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class BlindStatus(str, Enum):
    # 盲评批次状态：已封存/揭榜评估中/已整组揭榜
    sealed = 'sealed'
    revealing = 'revealing'
    revealed = 'revealed'
class CandidateStatus(str, Enum):
    # 候选参数状态：已冻结/排队/评估中/已完成/失败
    frozen = 'frozen'
    queued = 'queued'
    running = 'running'
    done = 'done'
    failed = 'failed'
