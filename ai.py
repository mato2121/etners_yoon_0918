"""AI 연동 (Anthropic Claude / Google Gemini 중 등록된 쪽을 자동으로 사용).

둘 다 키가 없으면 모든 함수가 조용히 None을 반환합니다. 즉, 이 모듈이 없어도
(키 미설정) 앱의 나머지 기능(기록 저장/조회 등)은 정상 동작해야 합니다.
"""
import json
import os
import re

from models import ENTRY_TYPE_LABELS

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def _get_setting():
    try:
        from models import AppSetting

        return AppSetting.get()
    except Exception:  # noqa: BLE001 - DB 컨텍스트 밖/스키마 불일치 등에서 호출돼도 죽지 않게
        # 실패한 쿼리를 그냥 무시하면 DB 트랜잭션이 "aborted" 상태로 남아, 같은 요청 안의
        # 이후 모든 쿼리가 연쇄적으로 실패한다. 반드시 롤백해서 세션을 정상 상태로 되돌린다.
        try:
            from models import db

            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def get_anthropic_api_key():
    """/settings 화면에서 저장한 키를 우선 사용하고, 없으면 환경변수를 본다."""
    setting = _get_setting()
    if setting and setting.anthropic_api_key:
        return setting.anthropic_api_key
    return os.environ.get("ANTHROPIC_API_KEY")


def get_gemini_api_key():
    """/settings 화면에서 저장한 키를 우선 사용하고, 없으면 환경변수를 본다."""
    setting = _get_setting()
    if setting and setting.gemini_api_key:
        return setting.gemini_api_key
    return os.environ.get("GEMINI_API_KEY")


def active_provider():
    """둘 다 등록돼 있으면 Gemini를 우선 사용한다. 하나도 없으면 None."""
    if get_gemini_api_key():
        return "gemini"
    if get_anthropic_api_key():
        return "anthropic"
    return None


def is_enabled():
    return active_provider() is not None


def _extract_json(text):
    """모델 응답에서 첫 번째 JSON 객체/배열만 안전하게 추출."""
    match = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


def _call(system, user, max_tokens=1500):
    """등록된 AI 제공자(Gemini 우선, 없으면 Claude)로 호출한다. 실패/미설정 시 None."""
    provider = active_provider()
    if provider == "gemini":
        return _call_gemini(system, user, max_tokens)
    if provider == "anthropic":
        return _call_anthropic(system, user, max_tokens)
    return None


def _call_anthropic(system, user, max_tokens):
    api_key = get_anthropic_api_key()
    if not api_key:
        return None
    try:
        import anthropic

        # Vercel 서버리스 콜드 스타트 등으로 첫 연결이 느릴 수 있어 타임아웃/재시도를 넉넉히 준다.
        client = anthropic.Anthropic(api_key=api_key, timeout=45.0, max_retries=4)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
    except Exception as exc:  # noqa: BLE001 - 네트워크/키 오류 등 무엇이든 AI 비활성화로 취급
        cause = getattr(exc, "__cause__", None)
        print(f"[ai] Claude 호출 실패: {exc!r} / cause={cause!r}")
        return None


def _call_gemini(system, user, max_tokens):
    api_key = get_gemini_api_key()
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        return response.text
    except Exception as exc:  # noqa: BLE001 - 네트워크/키 오류 등 무엇이든 AI 비활성화로 취급
        print(f"[ai] Gemini 호출 실패: {exc!r}")
        return None


def _normalize_title(title):
    return (title or "").strip().lower()


def update_exception_rules(client_row, triggering_entry_id=None):
    """새 기록을 반영해 예외 규칙 전체를 다시 정리하고, 이전 상태와의 차이를 RuleChangeLog로 남긴다."""
    from models import ExceptionRule, KnowledgeEntry, RuleChangeLog, db

    existing = client_row.rules.all()
    existing_text = (
        "\n".join(
            f"- {r.title}: {r.detail}"
            + (f" (매월 {r.day_of_month}일 관련 마감/리마인더 있음)" if r.day_of_month else "")
            for r in existing
        )
        or "(아직 없음)"
    )

    recent_entries = (
        KnowledgeEntry.query.filter_by(client_id=client_row.id)
        .order_by(KnowledgeEntry.created_at.desc())
        .limit(20)
        .all()
    )
    entries_text = "\n".join(
        f"[{ENTRY_TYPE_LABELS.get(e.entry_type, e.entry_type)}] {e.content}"
        for e in reversed(recent_entries)
    )

    system = (
        "너는 급여/복리후생 아웃소싱 회사의 고객사별 예외 처리 규칙을 관리하는 보조 도구다. "
        "담당자가 남긴 메모/정정이력/메시지를 바탕으로, 이 고객사를 처리할 때 반드시 지켜야 할 "
        "'예외 규칙'들을 짧고 실무적인 한국어로 정리한다. 같은 내용은 하나로 합치고, 오래되어 "
        "더 이상 유효하지 않아 보이는 규칙은 제외한다. 만약 어떤 규칙이 '매월 특정 날짜까지 "
        "무엇을 해야 한다'는 반복 마감/리마인더 성격을 가지면 day_of_month(1~31)를 채우고, "
        "아니면 null로 둔다. 반드시 JSON만 출력한다."
    )
    user = (
        f"[기존 예외 규칙]\n{existing_text}\n\n"
        f"[담당자가 남긴 최근 기록 전체]\n{entries_text}\n\n"
        "위 내용을 반영해 최신 상태의 예외 규칙 전체 목록을 다시 만들어줘. "
        '다음 형식의 JSON으로만 응답: {"rules": [{"title": "규칙 제목(10자 내외)", '
        '"detail": "구체적인 처리 방법 1~2문장", "day_of_month": 20 또는 null}]}'
    )

    raw = _call(system, user)
    if not raw:
        return False

    parsed = _extract_json(raw)
    if not parsed or "rules" not in parsed:
        return False

    new_rules = []
    for rule in parsed["rules"]:
        title = (rule.get("title") or "").strip()
        detail = (rule.get("detail") or "").strip()
        day = rule.get("day_of_month")
        day = int(day) if isinstance(day, (int, float)) and 1 <= int(day) <= 31 else None
        if title and detail:
            new_rules.append({"title": title, "detail": detail, "day_of_month": day})

    # --- 변경 이력(diff) 계산: 제목 기준으로 기존 규칙과 비교 ---
    existing_by_key = {_normalize_title(r.title): r for r in existing}
    new_by_key = {_normalize_title(r["title"]): r for r in new_rules}

    for key, new_rule in new_by_key.items():
        old_rule = existing_by_key.get(key)
        if old_rule is None:
            db.session.add(
                RuleChangeLog(
                    client_id=client_row.id,
                    triggering_entry_id=triggering_entry_id,
                    change_type="added",
                    rule_title=new_rule["title"],
                    before_detail=None,
                    after_detail=new_rule["detail"],
                )
            )
        elif old_rule.detail.strip() != new_rule["detail"].strip():
            db.session.add(
                RuleChangeLog(
                    client_id=client_row.id,
                    triggering_entry_id=triggering_entry_id,
                    change_type="updated",
                    rule_title=new_rule["title"],
                    before_detail=old_rule.detail,
                    after_detail=new_rule["detail"],
                )
            )

    for key, old_rule in existing_by_key.items():
        if key not in new_by_key:
            db.session.add(
                RuleChangeLog(
                    client_id=client_row.id,
                    triggering_entry_id=triggering_entry_id,
                    change_type="removed",
                    rule_title=old_rule.title,
                    before_detail=old_rule.detail,
                    after_detail=None,
                )
            )

    ExceptionRule.query.filter_by(client_id=client_row.id).delete()
    for rule in new_rules:
        db.session.add(
            ExceptionRule(
                client_id=client_row.id,
                title=rule["title"],
                detail=rule["detail"],
                day_of_month=rule["day_of_month"],
            )
        )
    db.session.commit()
    return True


def generate_briefing(client_row):
    """고객사 처리를 시작할 때 보여줄 짧은 브리핑 생성."""
    rules = client_row.rules.all()
    if not rules:
        return None

    rules_text = "\n".join(f"- {r.title}: {r.detail}" for r in rules)

    system = (
        "너는 급여/복리후생 담당자가 특정 고객사 업무를 막 시작하려는 순간에 보여줄 "
        "'브리핑'을 작성하는 보조 도구다. 담당자가 실수하기 쉬운 부분 위주로, "
        "3~6개의 짧은 불릿 포인트로 요약한다. 서론/결론 없이 불릿만 출력한다."
    )
    user = f"[이 고객사의 예외 규칙]\n{rules_text}\n\n오늘 이 고객사 업무를 시작하는 담당자에게 줄 브리핑을 작성해줘."

    raw = _call(system, user, max_tokens=600)
    return raw.strip() if raw else None


def generate_handover_manual(client_row, handover_context=None):
    """전체 인수인계 매뉴얼(구조화된 섹션 JSON)을 생성.

    handover_context: {"from_name", "to_name", "reason_label", "note"} 형태의 선택적 dict.
    """
    from models import KnowledgeEntry

    rules = client_row.rules.all()
    rules_text = "\n".join(f"- {r.title}: {r.detail}" for r in rules) or "(등록된 예외 규칙 없음)"

    entries = (
        KnowledgeEntry.query.filter_by(client_id=client_row.id)
        .order_by(KnowledgeEntry.created_at.asc())
        .all()
    )
    entries_text = "\n".join(
        f"[{e.created_at.strftime('%Y-%m-%d')}] [{ENTRY_TYPE_LABELS.get(e.entry_type, e.entry_type)}] {e.content}"
        for e in entries
    ) or "(등록된 기록 없음)"

    handover_text = "(별도 인수인계 정보 없음)"
    if handover_context:
        parts = []
        if handover_context.get("from_name"):
            parts.append(f"인계자: {handover_context['from_name']}")
        if handover_context.get("to_name"):
            parts.append(f"인수자: {handover_context['to_name']}")
        if handover_context.get("reason_label"):
            parts.append(f"사유: {handover_context['reason_label']}")
        if handover_context.get("note"):
            parts.append(f"인계자가 남긴 메모: {handover_context['note']}")
        if parts:
            handover_text = "\n".join(parts)

    system = (
        "너는 급여/복리후생 담당자의 업무 인수인계 매뉴얼을 작성하는 보조 도구다. "
        "이 문서는 이 고객사를 처음 맡는 후임자가 읽고 바로 업무를 이어받을 수 있어야 한다. "
        "인수인계 정보(인계자/인수자/사유/메모)가 주어지면 문서 도입부에서 그 내용을 자연스럽게 "
        "언급하며 인수자를 직접 향한 어조로 쓴다. 반드시 JSON만 출력한다."
    )
    user = (
        f"[고객사명]\n{client_row.name}\n\n"
        f"[인수인계 정보]\n{handover_text}\n\n"
        f"[현재 정리된 예외 규칙]\n{rules_text}\n\n"
        f"[담당자가 남긴 전체 기록(시간순)]\n{entries_text}\n\n"
        "위 내용을 바탕으로 인수인계 매뉴얼을 작성해줘. 다음 섹션을 포함하되 내용이 없으면 "
        "해당 섹션은 생략해도 된다: 인수인계 안내(인계자/인수자/사유/메모가 있을 때만), 고객사 개요, "
        "상여금/급여 처리 시 유의사항, 4대보험 신고 유의사항, 기타 예외 규칙, 최근 정정이력 요약. "
        '다음 형식의 JSON으로만 응답: {"sections": [{"heading": "섹션 제목", '
        '"body": "본문(여러 문장 가능, 필요하면 줄바꿈 \\n 사용)"}]}'
    )

    raw = _call(system, user, max_tokens=2000)
    if not raw:
        return None

    parsed = _extract_json(raw)
    if not parsed or "sections" not in parsed:
        return None

    return parsed["sections"]


# ---------------------------------------------------------------------------
# 자연어 질의 검색 (RAG 방식 — 임베딩 없이 키워드로 1차 추출 후 AI가 답변)
# ---------------------------------------------------------------------------
def find_relevant_entries(query, entries, top_k=8):
    """아주 단순한 키워드 매칭으로 관련성 높은 기록을 추린다 (벡터 검색 없는 1차 버전)."""
    keywords = [w for w in re.split(r"[\s,.?!]+", query) if len(w) > 1]
    if not keywords:
        return list(entries)[:top_k]

    def score(entry):
        return sum(entry.content.count(k) for k in keywords)

    scored = [(score(e), e) for e in entries]
    scored = [pair for pair in scored if pair[0] > 0]
    if not scored:
        return list(entries)[:top_k]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [e for _, e in scored[:top_k]]


def answer_question(query, scope_label, relevant_entries):
    """관련 기록을 근거로 담당자의 질문에 답한다. AI 비활성 시 관련 기록만 나열."""
    if not relevant_entries:
        return "관련된 기록을 찾지 못했습니다. 다른 표현으로 다시 질문해보세요."

    entries_text = "\n".join(
        f"- [{e.client.name} · {e.created_at.strftime('%Y-%m-%d')} · "
        f"{ENTRY_TYPE_LABELS.get(e.entry_type, e.entry_type)}] {e.content}"
        for e in relevant_entries
    )

    if not is_enabled():
        return (
            "⚠️ AI 키가 없어 답변을 생성할 수 없습니다. 관련 기록만 나열합니다.\n\n"
            + entries_text
        )

    system = (
        "너는 급여/복리후생 담당자의 질문에, 주어진 사내 기록만 근거로 답하는 보조 도구다. "
        "기록에 없는 내용은 추측하지 말고 '관련 기록에서 확인되지 않습니다'라고 답한다. "
        "답변은 3~4문장 이내로 간결하게 작성한다."
    )
    user = f"[검색 범위]\n{scope_label}\n\n[관련 기록]\n{entries_text}\n\n[질문]\n{query}"

    raw = _call(system, user, max_tokens=500)
    return raw.strip() if raw else "답변 생성에 실패했습니다. 잠시 후 다시 시도해주세요."
