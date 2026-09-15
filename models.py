"""업무 연속성 매뉴얼 자동 생성기 - 데이터 모델."""
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

ENTRY_TYPE_LABELS = {
    "note": "메모",
    "correction": "정정이력",
    "message": "메시지",
}

HANDOVER_REASON_LABELS = {
    "vacation": "휴가",
    "sick_leave": "병가",
    "resignation": "퇴사",
    "other": "기타",
}

RULE_CHANGE_LABELS = {
    "added": "추가",
    "updated": "변경",
    "removed": "삭제",
}

STALE_RULE_DAYS = 90  # 이 기간 동안 갱신 안 되면 "오래됨"으로 표시


class AppSetting(db.Model):
    """앱 전역 설정 (딱 1행만 사용). Anthropic API 키를 터미널 없이 화면에서 저장하기 위함."""

    id = db.Column(db.Integer, primary_key=True)
    anthropic_api_key = db.Column(db.String(200), nullable=True)

    @staticmethod
    def get():
        setting = AppSetting.query.get(1)
        if not setting:
            setting = AppSetting(id=1)
            db.session.add(setting)
            db.session.commit()
        return setting


class User(UserMixin, db.Model):
    """급여/복리후생 담당 직원 계정."""

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password):
        from werkzeug.security import generate_password_hash

        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        from werkzeug.security import check_password_hash

        return check_password_hash(self.password_hash, raw_password)


class Client(db.Model):
    """담당 고객사."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship(
        "KnowledgeEntry", backref="client", cascade="all, delete-orphan", lazy="dynamic"
    )
    rules = db.relationship(
        "ExceptionRule", backref="client", cascade="all, delete-orphan", lazy="dynamic"
    )
    manuals = db.relationship(
        "HandoverManual", backref="client", cascade="all, delete-orphan", lazy="dynamic"
    )
    rule_changes = db.relationship(
        "RuleChangeLog", backref="client", cascade="all, delete-orphan", lazy="dynamic"
    )

    @property
    def last_activity_at(self):
        latest = self.entries.order_by(KnowledgeEntry.created_at.desc()).first()
        return latest.created_at if latest else self.created_at


class KnowledgeEntry(db.Model):
    """담당자가 남기는 원본 기록 (메모/정정이력/메시지) + 선택적 첨부파일."""

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    entry_type = db.Column(db.String(20), nullable=False, default="note")
    content = db.Column(db.Text, nullable=False)
    attachment_path = db.Column(db.String(400), nullable=True)  # uploads/ 기준 상대 경로
    attachment_original_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship("User")

    @property
    def type_label(self):
        return ENTRY_TYPE_LABELS.get(self.entry_type, self.entry_type)

    @property
    def is_image_attachment(self):
        if not self.attachment_original_name:
            return False
        return self.attachment_original_name.lower().rsplit(".", 1)[-1] in (
            "png", "jpg", "jpeg", "gif", "webp"
        )


class ExceptionRule(db.Model):
    """AI가 유지·관리하는 구조화된 예외 규칙."""

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text, nullable=False)
    day_of_month = db.Column(db.Integer, nullable=True)  # 매달 반복되는 마감/리마인드 일자(1~31)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def days_since_update(self):
        return (datetime.utcnow() - self.updated_at).days

    @property
    def is_stale(self):
        return self.days_since_update > STALE_RULE_DAYS


class RuleChangeLog(db.Model):
    """예외 규칙이 AI에 의해 갱신될 때마다 남는 변경 이력(추가/변경/삭제)."""

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    triggering_entry_id = db.Column(db.Integer, db.ForeignKey("knowledge_entry.id"), nullable=True)
    change_type = db.Column(db.String(10), nullable=False)  # added|updated|removed
    rule_title = db.Column(db.String(200), nullable=False)
    before_detail = db.Column(db.Text, nullable=True)
    after_detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def change_label(self):
        return RULE_CHANGE_LABELS.get(self.change_type, self.change_type)


class HandoverManual(db.Model):
    """생성된 인수인계 매뉴얼 (여러 건 보관) — 인계자/인수자/사유 포함."""

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)  # 인계자
    handed_to_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)  # 인수자
    reason = db.Column(db.String(20), nullable=True)  # vacation|sick_leave|resignation|other
    handover_note = db.Column(db.Text, nullable=True)  # 인계자가 인수자에게 남기는 메모
    content_json = db.Column(db.Text, nullable=False)  # [{"heading": "...", "body": "..."}]
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    generated_by = db.relationship("User", foreign_keys=[generated_by_id])
    handed_to = db.relationship("User", foreign_keys=[handed_to_id])

    @property
    def reason_label(self):
        return HANDOVER_REASON_LABELS.get(self.reason, None)

    @property
    def sections(self):
        import json

        try:
            return json.loads(self.content_json)
        except (ValueError, TypeError):
            return []
