# ==============================================================================
# 소비자 계약 / 환불 관련 법령 데이터 수집
#
# 국가법령정보센터 OPEN API를 이용하여
# 소비자 분쟁과 관련된 법령을 조회한 뒤,
# 조문 단위로 정리하여 CSV 파일로 저장한다.
#
# 저장 위치:
#   현재 프로그램 : 99_조별과제/consumer_contract_refund.py
#   결과 CSV     : data/consumer_contract_laws.csv
#
# 이후 RAG 프로젝트에서는
# "RAG_검색문서" 컬럼을 임베딩해서 사용할 수 있다.
# ==============================================================================


import os
import requests
import pandas as pd

from pathlib import Path
from dotenv import load_dotenv


# ==============================================================================
# 1. API 인증키 설정
# ==============================================================================

# .env 파일에 저장되어 있는 환경변수를 불러온다.
load_dotenv()


# 국가법령정보 공동활용 OPEN API 인증키
#
# .env 파일에 아래와 같이 저장하면 된다.
#
# LAW_OC=발급받은_API인증키
#
LAW_OC = os.getenv("LAW_OC")


# .env에 인증키가 없는 경우 실행 중 직접 입력받는다.
if not LAW_OC:
    LAW_OC = input("국가법령정보 OPEN API 인증키(OC)를 입력하세요: ").strip()


# 인증키가 입력되지 않은 경우 프로그램 종료
if not LAW_OC:
    raise ValueError("OPEN API 인증키(OC)가 필요합니다.")


# ==============================================================================
# 2. 저장할 data 폴더 설정
# ==============================================================================

# 현재 실행 중인 Python 파일의 폴더
#
# 예:
# .../99_조별과제
SCRIPT_DIR = Path(__file__).resolve().parent


# 현재 프로그램 폴더의 상위 폴더에 있는 data 폴더
#
# 예:
# .../99_조별과제
#        ↑
# .../data
DATA_DIR = SCRIPT_DIR.parent / "data"


# data 폴더가 없으면 자동 생성
DATA_DIR.mkdir(parents=True, exist_ok=True)


# 최종 CSV 저장 파일
OUTPUT_FILE = DATA_DIR / "consumer_contract_laws.csv"


# ==============================================================================
# 3. 수집할 법령 목록
# ==============================================================================

# 소비자 계약 / 환불 관련 법률
#
# 처음부터 너무 많은 법률을 넣기보다는
# 소비자가 실제 생활에서 자주 접할 수 있는 법률부터 수집한다.
#
# 필요하면 나중에 목록에 법령명을 추가하면 된다.
LAW_NAMES = [

    # 인터넷 쇼핑 / 온라인 구매 / 청약철회 / 반품
    "전자상거래 등에서의 소비자보호에 관한 법률",

    # 소비자의 기본적인 권리와 소비자분쟁 관련 기본 법률
    "소비자기본법",

    # 방문판매 / 전화권유판매 / 다단계판매 등의 계약
    "방문판매 등에 관한 법률",

    # 불공정 약관 관련
    "약관의 규제에 관한 법률",

    # 할부 구매 / 장기 할부 등의 계약
    "할부거래에 관한 법률",
]


# ==============================================================================
# 4. API 주소
# ==============================================================================

# 법령 검색 API
LAW_SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"

# 법령 본문 조회 API
LAW_DETAIL_URL = "https://www.law.go.kr/DRF/lawService.do"


# ==============================================================================
# 5. 값을 항상 list 형태로 변환하는 함수
# ==============================================================================

def to_list(value):
    """
    국가법령정보 API의 JSON 구조에서는
    데이터가 1개일 때 dict,
    여러 개일 때 list가 되는 경우가 있다.

    이후 처리 코드를 단순하게 만들기 위해
    항상 list 형태로 변환한다.
    """

    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


# ==============================================================================
# 6. 항 / 호 / 목의 세부 내용을 추출하는 함수
# ==============================================================================

def extract_detail_text(node):
    """
    하나의 조문 안에 들어있는

    항
    호
    목

    내용을 재귀적으로 찾아 문자열로 합친다.

    예:
        제17조 청약철회
            ① 소비자는 ...
                1. ...
                2. ...

    → 위 내용을 하나의 문자열로 합쳐 RAG 검색에 사용하기 쉽게 만든다.
    """

    result = []


    # 데이터가 list인 경우
    if isinstance(node, list):

        for item in node:
            result.extend(extract_detail_text(item))


    # 데이터가 dict인 경우
    elif isinstance(node, dict):

        # 항 내용
        if node.get("항내용"):
            result.append(str(node["항내용"]).strip())

        # 호 내용
        if node.get("호내용"):
            result.append(str(node["호내용"]).strip())

        # 목 내용
        if node.get("목내용"):
            result.append(str(node["목내용"]).strip())


        # 하위 구조도 계속 탐색
        for key in ["항", "호", "목"]:

            if key in node:
                result.extend(
                    extract_detail_text(node[key])
                )


    return result


# ==============================================================================
# 7. 법령명을 검색하여 법령 ID를 찾는 함수
# ==============================================================================

def search_law(law_name):
    """
    법령명을 이용해 국가법령정보센터에서 법령을 검색한다.

    검색 결과 중 법령명이 정확히 일치하는 현행 법령을 찾아 반환한다.

    반환되는 주요 정보:
        법령ID
        법령명
        법령일련번호
        시행일자
        소관부처
        법령종류
    """


    # API 요청 파라미터
    params = {
        "OC": LAW_OC,

        # 법령 검색
        "target": "law",

        # JSON 형식으로 결과 요청
        "type": "JSON",

        # 법령명 검색
        "search": 1,

        # 검색할 법령명
        "query": law_name,

        # 최대 100개까지 검색
        "display": 100,

        # 첫 번째 페이지
        "page": 1,

        # 모바일 목록 API 옵션
        "mobileYn": "Y"
    }


    # 국가법령정보센터 API 호출
    response = requests.get(
        LAW_SEARCH_URL,
        params=params,
        timeout=30
    )


    # HTTP 오류가 발생하면 예외 발생
    response.raise_for_status()


    # JSON 데이터로 변환
    data = response.json()


    # 검색결과 목록
    law_search = data.get("LawSearch", {})

    laws = to_list(
        law_search.get("law")
    )


    # 검색된 법령 중
    # 이름이 정확하게 일치하는 법령 찾기
    for law in laws:

        found_name = law.get("법령명한글", "")

        if found_name.strip() == law_name.strip():

            return law


    # 정확히 일치하는 법령을 찾지 못한 경우
    return None


# ==============================================================================
# 8. 법령 ID로 법령 전체 본문 가져오기
# ==============================================================================

def get_law_detail(law_id):
    """
    법령ID를 이용하여 해당 법령의 전체 본문을 조회한다.

    여기에는
        법령 기본정보
        조문
        항
        호
        목
    등이 포함된다.
    """


    params = {

        # API 인증키
        "OC": LAW_OC,

        # 법령 본문 조회
        "target": "law",

        # JSON 결과 요청
        "type": "JSON",

        # 법령 ID
        "ID": law_id
    }


    # API 요청
    response = requests.get(
        LAW_DETAIL_URL,
        params=params,
        timeout=30
    )


    # HTTP 오류 확인
    response.raise_for_status()


    # JSON 반환
    return response.json()


# ==============================================================================
# 9. 법령 JSON을 CSV용 행 데이터로 변환
# ==============================================================================

def parse_law(law_json):
    """
    법령 전체 JSON에서 조문을 하나씩 꺼내

    조문 1개 = CSV 1행

    형태의 데이터로 변환한다.
    """


    rows = []


    # 최상위 "법령" 데이터
    law = law_json.get("법령", {})


    # --------------------------------------------------------------------------
    # 법령 기본 정보
    # --------------------------------------------------------------------------

    basic_info = law.get("기본정보", {})


    # 법령명
    law_name = basic_info.get(
        "법령명_한글",
        ""
    )


    # 법령 ID
    law_id = basic_info.get(
        "법령ID",
        ""
    )


    # 시행일자
    effective_date = basic_info.get(
        "시행일자",
        ""
    )


    # 공포일자
    proclamation_date = basic_info.get(
        "공포일자",
        ""
    )


    # 법령 종류
    #
    # API에서 다음과 같이 들어올 수 있음
    #
    # {
    #     "content": "법률",
    #     "법종구분코드": "A0002"
    # }
    law_type_data = basic_info.get(
        "법종구분",
        {}
    )

    if isinstance(law_type_data, dict):

        law_type = law_type_data.get(
            "content",
            ""
        )

    else:

        law_type = str(law_type_data)


    # 소관부처
    ministry_data = basic_info.get(
        "소관부처",
        {}
    )

    if isinstance(ministry_data, dict):

        ministry = ministry_data.get(
            "content",
            ""
        )

    else:

        ministry = str(ministry_data)


    # --------------------------------------------------------------------------
    # 법령 조문 목록
    # --------------------------------------------------------------------------

    article_data = law.get(
        "조문",
        {}
    )


    articles = to_list(
        article_data.get("조문단위")
    )


    # --------------------------------------------------------------------------
    # 조문 하나씩 처리
    # --------------------------------------------------------------------------

    for article in articles:


        # "제1장 총칙" 같은 장/절 표시가 아니라
        # 실제 법 조문만 사용
        if article.get("조문여부") != "조문":
            continue


        # 조문번호
        article_number = article.get(
            "조문번호",
            ""
        )


        # 조문의 가지번호
        #
        # 예:
        # 제10조의2
        #
        article_branch_number = article.get(
            "조문가지번호",
            ""
        )


        # 조문 제목
        article_title = article.get(
            "조문제목",
            ""
        )


        # 조문 본문
        article_content = article.get(
            "조문내용",
            ""
        )


        # ----------------------------------------------------------------------
        # 항 / 호 / 목 내용 추출
        # ----------------------------------------------------------------------

        detail_parts = []


        if "항" in article:

            detail_parts.extend(
                extract_detail_text(
                    article["항"]
                )
            )


        if "호" in article:

            detail_parts.extend(
                extract_detail_text(
                    article["호"]
                )
            )


        if "목" in article:

            detail_parts.extend(
                extract_detail_text(
                    article["목"]
                )
            )


        # 세부 내용을 하나의 문자열로 합침
        detail_content = "\n".join(
            detail_parts
        )


        # ----------------------------------------------------------------------
        # 조문 전체 내용
        # ----------------------------------------------------------------------

        # 조문 본문 + 항/호/목을 하나의 문자열로 합친다.
        full_content_parts = []


        if article_content:
            full_content_parts.append(
                article_content.strip()
            )


        if detail_content:
            full_content_parts.append(
                detail_content.strip()
            )


        full_content = "\n".join(
            full_content_parts
        )


        # ----------------------------------------------------------------------
        # RAG 임베딩용 검색 문서 생성
        # ----------------------------------------------------------------------

        # 나중에 SentenceTransformer로 임베딩할 때
        # 이 컬럼 하나를 바로 사용할 수 있도록 구성한다.
        #
        # 예:
        #
        # 전자상거래 등에서의 소비자보호에 관한 법률
        # 제17조 청약철회등
        # 소비자는 ...
        #
        rag_document = (
            f"법령명: {law_name}\n"
            f"조문: 제{article_number}조"
        )


        # 제10조의2 같은 조문인 경우
        if article_branch_number not in ["", None, "0", 0]:

            rag_document += (
                f"의{article_branch_number}"
            )


        # 조문 제목 추가
        if article_title:

            rag_document += (
                f" ({article_title})"
            )


        rag_document += (
            f"\n내용: {full_content}"
        )


        # ----------------------------------------------------------------------
        # 출처 URL
        # ----------------------------------------------------------------------

        source_url = (
            "https://www.law.go.kr/"
            f"법령/{law_name}"
        )


        # ----------------------------------------------------------------------
        # CSV 한 행 생성
        # ----------------------------------------------------------------------

        rows.append({

            "법령ID": law_id,

            "법령명": law_name,

            "법령종류": law_type,

            "소관부처": ministry,

            "시행일자": effective_date,

            "공포일자": proclamation_date,

            "조문번호": article_number,

            "조문가지번호": article_branch_number,

            "조문제목": article_title,

            "조문내용": article_content,

            "세부내용": detail_content,

            "조문전체내용": full_content,

            # 나중에 임베딩할 때 사용할 컬럼
            "RAG_검색문서": rag_document,

            # 법령 원문 확인용
            "출처URL": source_url
        })


    return rows


# ==============================================================================
# 10. 전체 법령 수집 시작
# ==============================================================================

all_rows = []


print()
print("=" * 70)
print("소비자 계약 / 환불 관련 법령 데이터 수집 시작")
print("=" * 70)
print()


# LAW_NAMES에 등록된 법률을 하나씩 처리
for law_name in LAW_NAMES:

    print(
        f"[검색] {law_name}"
    )


    try:

        # ----------------------------------------------------------------------
        # 법령명 검색
        # ----------------------------------------------------------------------

        law_info = search_law(
            law_name
        )


        # 법령을 찾지 못한 경우
        if law_info is None:

            print(
                f"  → 법령을 찾지 못했습니다."
            )

            continue


        # 법령 ID
        law_id = law_info.get(
            "법령ID"
        )


        print(
            f"  → 법령ID: {law_id}"
        )


        # ----------------------------------------------------------------------
        # 법령 전체 본문 조회
        # ----------------------------------------------------------------------

        law_json = get_law_detail(
            law_id
        )


        # ----------------------------------------------------------------------
        # 조문 단위 데이터로 변환
        # ----------------------------------------------------------------------

        rows = parse_law(
            law_json
        )


        # 전체 결과에 추가
        all_rows.extend(
            rows
        )


        print(
            f"  → {len(rows)}개 조문 수집 완료"
        )


    except Exception as error:

        # 한 법령에서 오류가 발생해도
        # 나머지 법령은 계속 처리
        print(
            f"  → 오류 발생: {error}"
        )


    print()


# ==============================================================================
# 11. DataFrame 생성
# ==============================================================================

df = pd.DataFrame(
    all_rows
)


# ==============================================================================
# 12. CSV 저장
# ==============================================================================

if len(df) > 0:

    # Excel에서 한글이 깨지지 않도록 utf-8-sig 사용
    df.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )


    print("=" * 70)
    print("법령 데이터 수집 완료")
    print("=" * 70)

    print(
        f"저장 건수 : {len(df)}"
    )

    print(
        f"저장 위치 : {OUTPUT_FILE}"
    )

    print()


    # 법령별 조문 수 확인
    print("법령별 수집 건수")
    print("-" * 70)

    print(
        df["법령명"].value_counts()
    )


    print()
    print("CSV 생성 완료.")


else:

    print("수집된 데이터가 없습니다.")
    print("API 인증키와 법령명을 확인해주세요.")