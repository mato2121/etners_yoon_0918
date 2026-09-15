"""최초 실행 시 데모 데이터를 심어서 빈 화면으로 시작하지 않게 한다.
AI 키가 없어도 앱의 기능(브리핑/매뉴얼 열람/Word 다운로드)을 바로 체험할 수 있도록,
예외 규칙과 인수인계 매뉴얼 1건은 고정 텍스트로 미리 만들어 둔다.
"""
import json
from datetime import datetime, timedelta

from models import (
    Client,
    ClientAccess,
    ExceptionRule,
    HandoverManual,
    KnowledgeEntry,
    RuleChangeLog,
    User,
    db,
)


def seed_if_empty():
    if Client.query.first():
        return  # 이미 데이터가 있으면 아무것도 하지 않음

    demo_user = User(username="demo", is_admin=True)
    demo_user.set_password("demo1234!")
    db.session.add(demo_user)

    newstaff_user = User(username="newstaff")
    newstaff_user.set_password("test1234")
    db.session.add(newstaff_user)
    db.session.flush()

    now = datetime.utcnow()

    # ---- 고객사 1: (주)한빛전자 ----
    hanbit = Client(name="(주)한빛전자", owner_id=demo_user.id, created_at=now - timedelta(days=120))
    db.session.add(hanbit)
    db.session.flush()

    db.session.add_all(
        [
            KnowledgeEntry(
                client_id=hanbit.id,
                author_id=demo_user.id,
                entry_type="note",
                content="상여금은 기본급 기준이 아니라 '통상임금' 기준으로 계산해야 함. 인사팀 요청사항.",
                created_at=now - timedelta(days=110),
            ),
            KnowledgeEntry(
                client_id=hanbit.id,
                author_id=demo_user.id,
                entry_type="correction",
                content="2025년 3월분 급여에서 식대 비과세 한도를 20만원으로 잘못 적용해 정정함(올바른 한도 확인 필요 시 인사팀 김과장에게 문의).",
                created_at=now - timedelta(days=60),
            ),
            KnowledgeEntry(
                client_id=hanbit.id,
                author_id=demo_user.id,
                entry_type="message",
                content="[인사팀 요청] 매월 4대보험 신고는 건강보험 → 국민연금 → 고용/산재 순서로 처리해달라고 함(내부 결재 순서 때문).",
                created_at=now - timedelta(days=20),
            ),
        ]
    )
    db.session.add_all(
        [
            ExceptionRule(
                client_id=hanbit.id,
                title="상여금 계산 기준",
                detail="기본급이 아닌 통상임금 기준으로 상여금을 계산한다. 인사팀 명시적 요청 사항.",
                updated_at=now - timedelta(days=100),
            ),
            ExceptionRule(
                client_id=hanbit.id,
                title="식대 비과세 한도",
                detail="비과세 한도는 20만원이 아니라 회사 규정상 별도 한도가 있으니 반영 전 인사팀 김과장에게 재확인한다.",
                updated_at=now - timedelta(days=55),
            ),
            ExceptionRule(
                client_id=hanbit.id,
                title="4대보험 신고 순서",
                detail="건강보험 → 국민연금 → 고용/산재 순서로 신고한다. 내부 결재 프로세스 때문에 순서를 반드시 지켜야 함.",
                day_of_month=10,
                updated_at=now - timedelta(days=18),
            ),
        ]
    )

    # ---- 규칙 변경 이력 예시 (식대 비과세 한도 규칙이 정정 사건을 계기로 어떻게 바뀌었는지) ----
    db.session.add_all(
        [
            RuleChangeLog(
                client_id=hanbit.id,
                change_type="added",
                rule_title="식대 비과세 한도",
                before_detail=None,
                after_detail="비과세 한도는 일반적으로 20만원이다.",
                created_at=now - timedelta(days=100),
            ),
            RuleChangeLog(
                client_id=hanbit.id,
                change_type="updated",
                rule_title="식대 비과세 한도",
                before_detail="비과세 한도는 일반적으로 20만원이다.",
                after_detail="비과세 한도는 20만원이 아니라 회사 규정상 별도 한도가 있으니 반영 전 인사팀 김과장에게 재확인한다.",
                created_at=now - timedelta(days=55),
            ),
        ]
    )

    db.session.add(
        HandoverManual(
            client_id=hanbit.id,
            generated_by_id=demo_user.id,
            handed_to_id=newstaff_user.id,
            reason="vacation",
            handover_note="이번 주 급한 처리 건은 없습니다. 다음 급여 마감은 25일이에요.",
            created_at=now - timedelta(days=15),
            content_json=json.dumps(
                [
                    {
                        "heading": "인수인계 안내",
                        "body": "demo님이 휴가로 인해 newstaff님에게 인수인계합니다. 이번 주 급한 처리 건은 없으며, 다음 급여 마감은 25일입니다.",
                    },
                    {
                        "heading": "고객사 개요",
                        "body": "(주)한빛전자 — 급여/4대보험 신고 담당. 인사팀 담당자: 김과장.",
                    },
                    {
                        "heading": "상여금/급여 처리 시 유의사항",
                        "body": "- 상여금은 기본급이 아닌 통상임금 기준으로 계산\n- 식대 비과세 한도는 일반적인 20만원과 다를 수 있어 반영 전 인사팀 재확인 필요",
                    },
                    {
                        "heading": "4대보험 신고 유의사항",
                        "body": "- 신고 순서: 건강보험 → 국민연금 → 고용/산재\n- 내부 결재 프로세스 때문에 순서를 반드시 지켜야 함",
                    },
                    {
                        "heading": "최근 정정이력 요약",
                        "body": "- 2025년 3월분 급여 식대 비과세 한도 오적용 정정 (재발 방지를 위해 위 유의사항 참고)",
                    },
                ],
                ensure_ascii=False,
            ),
        )
    )

    # ---- 고객사 2: 그린푸드 물류센터 ----
    green = Client(name="그린푸드 물류센터", owner_id=demo_user.id, created_at=now - timedelta(days=45))
    db.session.add(green)
    db.session.flush()

    db.session.add_all(
        [
            KnowledgeEntry(
                client_id=green.id,
                author_id=demo_user.id,
                entry_type="note",
                content="이 회사는 3교대 근무라 야간수당 계산 시 22시~06시 구간을 반드시 따로 집계해야 함.",
                created_at=now - timedelta(days=40),
            ),
            KnowledgeEntry(
                client_id=green.id,
                author_id=demo_user.id,
                entry_type="message",
                content="현장 관리자가 매월 25일까지 근태 엑셀을 보내주기로 함. 늦어지면 급여 마감일이 밀리니 20일경 미리 요청할 것.",
                created_at=now - timedelta(days=10),
            ),
        ]
    )
    db.session.add_all(
        [
            ExceptionRule(
                client_id=green.id,
                title="3교대 야간수당",
                detail="22시~06시 근무 구간을 별도로 집계해 야간수당을 계산한다.",
                updated_at=now - timedelta(days=38),
            ),
            ExceptionRule(
                client_id=green.id,
                title="근태 자료 수급 일정",
                detail="현장 관리자가 매월 25일까지 근태 엑셀을 전달. 지연 방지를 위해 20일경 미리 리마인드 요청한다.",
                day_of_month=20,
                updated_at=now - timedelta(days=8),
            ),
        ]
    )

    # ---- 고객사 3: 무한상사 ----
    muhan = Client(name="무한상사", owner_id=demo_user.id, created_at=now - timedelta(days=90))
    db.session.add(muhan)
    db.session.flush()

    db.session.add_all(
        [
            KnowledgeEntry(
                client_id=muhan.id,
                author_id=demo_user.id,
                entry_type="note",
                content="영업직 판매수당은 매출 확정일이 아니라 '입금 완료일' 기준으로 정산해야 함. 이 회사만의 규정.",
                created_at=now - timedelta(days=80),
            ),
            KnowledgeEntry(
                client_id=muhan.id,
                author_id=demo_user.id,
                entry_type="message",
                content="[대표님 요청] 명절 상여금은 다른 고객사와 달리 추석/설 전전날이 아니라 일주일 전에 미리 지급해달라고 함.",
                created_at=now - timedelta(days=50),
            ),
            KnowledgeEntry(
                client_id=muhan.id,
                author_id=demo_user.id,
                entry_type="correction",
                content="지난달 판매수당을 매출 확정일 기준으로 잘못 계산해서 정정 신고함. 담당자 교체 시 반드시 인지시켜야 할 사항.",
                created_at=now - timedelta(days=12),
            ),
        ]
    )
    db.session.add_all(
        [
            ExceptionRule(
                client_id=muhan.id,
                title="판매수당 정산 기준",
                detail="매출 확정일이 아닌 '입금 완료일' 기준으로 영업직 판매수당을 정산한다.",
                updated_at=now - timedelta(days=60),
            ),
            ExceptionRule(
                client_id=muhan.id,
                title="명절 상여금 지급일",
                detail="추석/설 연휴 시작 일주일 전에 미리 지급한다 (일반적인 전전날 지급과 다름).",
                updated_at=now - timedelta(days=45),
            ),
        ]
    )

    # ---- 고객사 4: (주)대성건설 ----
    daesung = Client(name="(주)대성건설", owner_id=demo_user.id, created_at=now - timedelta(days=70))
    db.session.add(daesung)
    db.session.flush()

    db.session.add_all(
        [
            KnowledgeEntry(
                client_id=daesung.id,
                author_id=demo_user.id,
                entry_type="note",
                content="일용직 근로자 비중이 높아서, 4대보험 중 국민연금·건강보험은 월 8일 이상 근무자만 가입 대상으로 별도 관리해야 함.",
                created_at=now - timedelta(days=65),
            ),
            KnowledgeEntry(
                client_id=daesung.id,
                author_id=demo_user.id,
                entry_type="note",
                content="산재보험료율은 본사가 아니라 현장별로 다르게 적용됨 (공사 종류에 따라 요율표 확인 필수).",
                created_at=now - timedelta(days=30),
            ),
        ]
    )
    db.session.add_all(
        [
            ExceptionRule(
                client_id=daesung.id,
                title="일용직 4대보험 가입 기준",
                detail="월 8일 이상 근무한 일용직만 국민연금·건강보험 가입 대상으로 별도 관리한다.",
                updated_at=now - timedelta(days=25),
            ),
            ExceptionRule(
                client_id=daesung.id,
                title="현장별 산재보험 요율",
                detail="산재보험료율은 본사 일괄 적용이 아니라 공사 현장(공종)별로 다르게 적용되므로 신고 전 요율표를 반드시 확인한다.",
                updated_at=now - timedelta(days=25),
            ),
        ]
    )

    # newstaff는 관리자가 아니므로, 데모에서 바로 모든 고객사를 볼 수 있도록 접근 권한을 부여해둔다
    # (실제로는 /admin/access 화면에서 관리자가 직접 부여/회수하게 된다).
    db.session.add_all(
        [
            ClientAccess(client_id=hanbit.id, user_id=newstaff_user.id),
            ClientAccess(client_id=green.id, user_id=newstaff_user.id),
            ClientAccess(client_id=muhan.id, user_id=newstaff_user.id),
            ClientAccess(client_id=daesung.id, user_id=newstaff_user.id),
        ]
    )

    db.session.commit()
