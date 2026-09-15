import calendar
import json
import os
import uuid
from datetime import date, datetime

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.utils import secure_filename

import ai
from docx_export import build_manual_docx
from models import (
    AppSetting,
    AuditLog,
    Client,
    ClientAccess,
    ExceptionRule,
    HandoverManual,
    KnowledgeEntry,
    RuleChangeLog,
    User,
    db,
)
from seed import seed_if_empty

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Vercel 서버리스는 배포 디렉터리가 읽기 전용이라 /tmp 에만 파일을 쓸 수 있고,
# 그마저도 인스턴스가 재시작되면 초기화됩니다(첨부파일은 영구 저장이 아님을 감안).
if os.environ.get("VERCEL"):
    UPLOAD_DIR = "/tmp/uploads"
else:
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
ALLOWED_ATTACHMENT_EXT = {"png", "jpg", "jpeg", "gif", "webp", "pdf", "xlsx", "xls", "docx", "txt"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32))
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 첨부파일 10MB 제한

database_url = os.environ.get("DATABASE_URL")
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
elif os.environ.get("VERCEL"):
    # Vercel 서버리스는 배포 디렉터리가 읽기 전용이라 /tmp 에만 쓸 수 있고,
    # 그마저도 인스턴스가 재시작되면 초기화됩니다(DATABASE_URL 미설정 시 임시 동작용).
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:////tmp/continuity.db"
else:
    db_path = os.path.join(BASE_DIR, "continuity.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "로그인이 필요합니다."
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()
    seed_if_empty()


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def _days_until_reminder(day_of_month, today=None):
    """이번 달 또는 다음 달 기준으로 해당 day_of_month까지 남은 일수를 계산."""
    today = today or date.today()

    def clamp(year, month, day):
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(day, last_day))

    this_month = clamp(today.year, today.month, day_of_month)
    if this_month >= today:
        target = this_month
    else:
        next_month = today.month + 1
        next_year = today.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        target = clamp(next_year, next_month, day_of_month)

    return (target - today).days, target


def _upcoming_reminders(within_days=7):
    reminders = []
    rules = (
        ExceptionRule.query.filter(ExceptionRule.day_of_month.isnot(None))
        .join(Client)
        .all()
    )
    for rule in rules:
        days_left, target_date = _days_until_reminder(rule.day_of_month)
        if 0 <= days_left <= within_days:
            reminders.append(
                {
                    "client": rule.client,
                    "rule": rule,
                    "days_left": days_left,
                    "target_date": target_date,
                }
            )
    reminders.sort(key=lambda r: r["days_left"])
    return reminders


def _save_attachment(file_storage, client_id):
    """첨부파일을 uploads/<client_id>/ 아래 안전한 파일명으로 저장. 반환: (경로, 원본파일명) 또는 (None, None)."""
    if not file_storage or not file_storage.filename:
        return None, None

    original_name = file_storage.filename
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in ALLOWED_ATTACHMENT_EXT:
        flash(f"허용되지 않는 파일 형식입니다 (.{ext}). 첨부 없이 저장했습니다.")
        return None, None

    client_dir = os.path.join(UPLOAD_DIR, str(client_id))
    os.makedirs(client_dir, exist_ok=True)

    safe_name = secure_filename(original_name) or "file"
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    file_storage.save(os.path.join(client_dir, stored_name))

    return f"{client_id}/{stored_name}", original_name


def check_client_access(client):
    """접근 권한이 없으면 403. 감사 로그 등 다른 처리에 앞서 라우트 맨 앞에서 호출한다."""
    if not client.is_accessible_by(current_user):
        abort(403)


def log_action(action, client=None, detail=None):
    db.session.add(
        AuditLog(
            actor_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            client_id=client.id if client else None,
            detail=detail,
        )
    )
    db.session.commit()


# ---------------------------------------------------------------------------
# 인증
# ---------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        error = None
        if len(username) < 3:
            error = "아이디는 3자 이상이어야 합니다."
        elif len(password) < 4:
            error = "비밀번호는 4자 이상이어야 합니다."
        elif password != password_confirm:
            error = "비밀번호가 서로 일치하지 않습니다."
        elif User.query.filter_by(username=username).first():
            error = "이미 사용 중인 아이디입니다."

        if error:
            flash(error)
            return render_template("register.html", username=username)

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        log_action("register")
        return redirect(url_for("dashboard"))

    return render_template("register.html", username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            log_action("login")
            return redirect(url_for("dashboard"))

        flash("아이디 또는 비밀번호가 올바르지 않습니다.")
        return render_template("login.html", username=username)

    return render_template("login.html", username="")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    log_action("logout")
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# 대시보드 / 고객사
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "name")

    query = Client.query
    if q:
        query = query.filter(Client.name.ilike(f"%{q}%"))
    all_clients = query.all()
    clients = [c for c in all_clients if c.is_accessible_by(current_user)]

    summary = {}
    for c in clients:
        summary[c.id] = {
            "rule_count": c.rules.count(),
            "entry_count": c.entries.count(),
            "last_activity": c.last_activity_at,
        }

    if sort == "recent":
        clients.sort(key=lambda c: summary[c.id]["last_activity"], reverse=True)
    elif sort == "entries":
        clients.sort(key=lambda c: summary[c.id]["entry_count"], reverse=True)
    else:
        clients.sort(key=lambda c: c.name)

    reminders = _upcoming_reminders()

    return render_template(
        "dashboard.html",
        clients=clients,
        summary=summary,
        ai_enabled=ai.is_enabled(),
        q=q,
        sort=sort,
        reminders=reminders,
    )


@app.route("/clients", methods=["POST"])
@login_required
def create_client():
    name = request.form.get("name", "").strip()
    if not name:
        flash("고객사명을 입력해주세요.")
        return redirect(url_for("dashboard"))
    if Client.query.filter_by(name=name).first():
        flash("이미 등록된 고객사명입니다.")
        return redirect(url_for("dashboard"))
    client = Client(name=name, owner_id=current_user.id)
    db.session.add(client)
    db.session.commit()
    log_action("create_client", client=client, detail=name)
    return redirect(url_for("dashboard"))


@app.route("/clients/<int:client_id>")
@login_required
def client_detail(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    check_client_access(client)

    rules = client.rules.order_by(ExceptionRule.title).all()
    entries = client.entries.order_by(KnowledgeEntry.created_at.desc()).all()
    manuals = client.manuals.order_by(HandoverManual.created_at.desc()).all()
    rule_changes = client.rule_changes.order_by(RuleChangeLog.created_at.desc()).limit(15).all()
    all_users = User.query.order_by(User.username).all()

    ai_enabled = ai.is_enabled()

    briefing = None
    briefing_message = None
    if not ai_enabled:
        briefing_message = (
            "AI 키가 설정되지 않아 브리핑을 생성할 수 없습니다. "
            "설정 화면에서 Anthropic API 키를 등록해주세요. "
            "아래는 지금까지 정리된 예외 규칙 목록입니다."
        )
    elif not rules:
        briefing_message = "아직 이 고객사에 대해 정리된 예외 규칙이 없습니다. 기록을 추가해보세요."
    else:
        briefing = ai.generate_briefing(client)
        if not briefing:
            briefing_message = "브리핑 생성에 실패했습니다. 잠시 후 다시 시도해주세요."

    return render_template(
        "client_detail.html",
        client=client,
        rules=rules,
        entries=entries,
        manuals=manuals,
        rule_changes=rule_changes,
        all_users=all_users,
        briefing=briefing,
        briefing_message=briefing_message,
        ai_enabled=ai_enabled,
    )


@app.route("/clients/<int:client_id>/entries", methods=["POST"])
@login_required
def add_entry(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    check_client_access(client)

    entry_type = request.form.get("entry_type", "note")
    content = request.form.get("content", "").strip()
    if entry_type not in ("note", "correction", "message"):
        entry_type = "note"

    if not content:
        flash("내용을 입력해주세요.")
        return redirect(url_for("client_detail", client_id=client.id))

    attachment_path, attachment_original_name = _save_attachment(
        request.files.get("attachment"), client.id
    )

    entry = KnowledgeEntry(
        client_id=client.id,
        author_id=current_user.id,
        entry_type=entry_type,
        content=content,
        attachment_path=attachment_path,
        attachment_original_name=attachment_original_name,
    )
    db.session.add(entry)
    db.session.commit()
    log_action("add_entry", client=client, detail=entry_type)

    if ai.is_enabled():
        updated = ai.update_exception_rules(client, triggering_entry_id=entry.id)
        if updated:
            flash("기록이 저장되었고, 예외 규칙이 AI로 갱신되었습니다.")
        else:
            flash("기록은 저장되었지만, 예외 규칙 갱신에는 실패했습니다.")
    else:
        flash("기록이 저장되었습니다. (AI 키가 없어 예외 규칙 자동 갱신은 건너뛰었습니다)")

    return redirect(url_for("client_detail", client_id=client.id))


@app.route("/uploads/<int:client_id>/<path:filename>")
@login_required
def uploaded_file(client_id, filename):
    client = db.session.get(Client, client_id) or abort(404)
    check_client_access(client)
    client_dir = os.path.join(UPLOAD_DIR, str(client_id))
    return send_from_directory(client_dir, filename)


@app.route("/clients/<int:client_id>/handover", methods=["POST"])
@login_required
def create_handover(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    check_client_access(client)

    if not ai.is_enabled():
        flash("AI 키가 설정되지 않아 인수인계 매뉴얼을 생성할 수 없습니다. 설정 화면에서 키를 등록해주세요.")
        return redirect(url_for("client_detail", client_id=client.id))

    handed_to_id = request.form.get("handed_to_id", type=int)
    reason = request.form.get("reason") or None
    handover_note = request.form.get("handover_note", "").strip() or None

    handed_to = db.session.get(User, handed_to_id) if handed_to_id else None

    from models import HANDOVER_REASON_LABELS

    handover_context = {
        "from_name": current_user.username,
        "to_name": handed_to.username if handed_to else None,
        "reason_label": HANDOVER_REASON_LABELS.get(reason),
        "note": handover_note,
    }

    sections = ai.generate_handover_manual(client, handover_context)
    if not sections:
        flash("매뉴얼 생성에 실패했습니다. 잠시 후 다시 시도해주세요.")
        return redirect(url_for("client_detail", client_id=client.id))

    manual = HandoverManual(
        client_id=client.id,
        generated_by_id=current_user.id,
        handed_to_id=handed_to.id if handed_to else None,
        reason=reason,
        handover_note=handover_note,
        content_json=json.dumps(sections, ensure_ascii=False),
    )
    db.session.add(manual)
    db.session.commit()
    log_action("create_handover", client=client, detail=f"v{manual.id}")
    return redirect(url_for("view_handover", client_id=client.id, manual_id=manual.id))


@app.route("/clients/<int:client_id>/handover/<int:manual_id>")
@login_required
def view_handover(client_id, manual_id):
    client = db.session.get(Client, client_id) or abort(404)
    check_client_access(client)
    manual = db.session.get(HandoverManual, manual_id) or abort(404)
    if manual.client_id != client.id:
        abort(404)
    return render_template("handover.html", client=client, manual=manual)


@app.route("/clients/<int:client_id>/handover/<int:manual_id>/export.docx")
@login_required
def export_handover(client_id, manual_id):
    client = db.session.get(Client, client_id) or abort(404)
    check_client_access(client)
    manual = db.session.get(HandoverManual, manual_id) or abort(404)
    if manual.client_id != client.id:
        abort(404)

    log_action("export_handover", client=client, detail=f"v{manual.id}")
    buffer = build_manual_docx(client.name, manual)
    filename = f"{client.name}_인수인계매뉴얼_{manual.created_at.strftime('%Y%m%d')}.docx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------------------------------------------------------------------
# 설정 (AI 키를 터미널 없이 화면에서 등록)
# ---------------------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if not current_user.is_admin:
        abort(403)

    setting = AppSetting.get()

    if request.method == "POST":
        new_key = request.form.get("anthropic_api_key", "").strip()
        if new_key:
            setting.anthropic_api_key = new_key
            db.session.commit()
            log_action("update_settings", detail="API 키 등록")
            flash("API 키가 저장되었습니다. AI 기능이 바로 활성화됩니다.")
        else:
            setting.anthropic_api_key = None
            db.session.commit()
            log_action("update_settings", detail="API 키 삭제")
            flash("API 키가 삭제되었습니다.")
        return redirect(url_for("settings"))

    masked_key = None
    if setting.anthropic_api_key:
        masked_key = "•" * 10 + setting.anthropic_api_key[-4:]
    elif os.environ.get("ANTHROPIC_API_KEY"):
        masked_key = "(환경변수 ANTHROPIC_API_KEY 사용 중)"

    return render_template(
        "settings.html",
        masked_key=masked_key,
        ai_enabled=ai.is_enabled(),
        has_db_key=bool(setting.anthropic_api_key),
    )


# ---------------------------------------------------------------------------
# 관리자용 전체 현황판
# ---------------------------------------------------------------------------
@app.route("/admin")
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        abort(403)

    all_rules = (
        ExceptionRule.query.join(Client).order_by(ExceptionRule.updated_at.asc()).all()
    )
    clients_without_rules = [c for c in Client.query.all() if c.rules.count() == 0]

    return render_template(
        "admin.html",
        rules=all_rules,
        clients_without_rules=clients_without_rules,
    )


# ---------------------------------------------------------------------------
# 자연어 질의 검색
# ---------------------------------------------------------------------------
@app.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    client_id = request.args.get("client_id", type=int)

    accessible_clients = [c for c in Client.query.all() if c.is_accessible_by(current_user)]

    answer = None
    matched_entries = []
    target_client = None
    if query:
        if client_id:
            target_client = db.session.get(Client, client_id) or abort(404)
            check_client_access(target_client)
            pool = target_client.entries.order_by(KnowledgeEntry.created_at.desc()).all()
            scope_label = target_client.name
        else:
            pool = [e for c in accessible_clients for e in c.entries]
            scope_label = "담당 고객사 전체"

        matched_entries = ai.find_relevant_entries(query, pool)
        answer = ai.answer_question(query, scope_label, matched_entries)
        log_action("search_query", client=target_client, detail=query[:100])

    return render_template(
        "search.html",
        query=query,
        answer=answer,
        matched_entries=matched_entries,
        clients=accessible_clients,
        selected_client_id=client_id,
        ai_enabled=ai.is_enabled(),
    )


# ---------------------------------------------------------------------------
# 관리자: 고객사 접근 권한 관리
# ---------------------------------------------------------------------------
@app.route("/admin/access")
@login_required
def admin_access():
    if not current_user.is_admin:
        abort(403)
    clients = Client.query.order_by(Client.name).all()
    users = User.query.order_by(User.username).all()
    return render_template("admin_access.html", clients=clients, users=users)


@app.route("/admin/access/grant", methods=["POST"])
@login_required
def grant_access():
    if not current_user.is_admin:
        abort(403)
    client_id = request.form.get("client_id", type=int)
    user_id = request.form.get("user_id", type=int)
    client = db.session.get(Client, client_id) or abort(404)
    target_user = db.session.get(User, user_id) or abort(404)

    exists = ClientAccess.query.filter_by(client_id=client_id, user_id=user_id).first()
    if not exists:
        db.session.add(ClientAccess(client_id=client_id, user_id=user_id))
        db.session.commit()
        log_action("grant_access", client=client, detail=f"→ {target_user.username}")
    return redirect(url_for("admin_access"))


@app.route("/admin/access/revoke", methods=["POST"])
@login_required
def revoke_access():
    if not current_user.is_admin:
        abort(403)
    access_id = request.form.get("access_id", type=int)
    access = db.session.get(ClientAccess, access_id) or abort(404)
    client, target_user = access.client, access.user
    db.session.delete(access)
    db.session.commit()
    log_action("revoke_access", client=client, detail=f"→ {target_user.username}")
    return redirect(url_for("admin_access"))


# ---------------------------------------------------------------------------
# 관리자: 감사 로그
# ---------------------------------------------------------------------------
@app.route("/admin/audit-log")
@login_required
def audit_log():
    if not current_user.is_admin:
        abort(403)
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return render_template("audit_log.html", logs=logs)


if __name__ == "__main__":
    app.run(debug=True)
