import pandas as pd  # 수집한 데이터를 테이블(DataFrame) 형태로 가공하고 CSV 파일로 저장하기 위한 라이브러리
from playwright.sync_api import (
    sync_playwright,  # 일반 파이썬(.py) 환경에 최적화된 Playwright 동기(Sync) API 모듈
)

# ==============================================================================
# Playwright 실행 및 브라우저 세션 관리
# ==============================================================================

# with 구문(Context Manager)을 사용하여 Playwright 객체를 자동으로 시작하고 작업 완료 시 자원을 반환
with sync_playwright() as pw:

    # 1. Chromium 기반 브라우저 실행
    # - headless=False: 크롤링 과정을 눈으로 확인할 수 있도록 실제 브라우저 창을 띄움
    browser = pw.chromium.launch(headless=False)

    # 2. 브라우저 내에 새로운 탭(페이지) 생성
    page = browser.new_page()

    # 3. 대상 웹사이트(스팀 한국어 메인 페이지)로 이동
    url = "https://store.steampowered.com/?l=koreana"
    page.goto(url)

    # 4. 페이지 동적 콘텐츠(JS) 로딩 완충을 위해 2000ms(2초)간 임시 대기
    page.wait_for_timeout(2000)

    # ==============================================================================
    # 웹 데이터 수집 (크롤링)
    # ==============================================================================

    # 5. 수집 대상 항목들의 CSS 선택자(Selector) 지정
    # - '#tab_newreleases_content': '신규 출시' 탭 아이디
    # - 'div.tab_content_items': 아이템들이 모여있는 컨테이너
    # - 'a.tab_row_item': 각 게임 항목을 클릭할 수 있는 개별 링크(<a> 태그)
    selector = "#tab_newreleases_content div.tab_content_items a.tab_row_item"

    # 6. 해당 선택자에 해당하는 요소가 웹 페이지 상에 출현할 때까지 최대 20,000ms(20초) 대기
    page.wait_for_selector(selector, timeout=20000)

    # 7. 해당 선택자와 일치하는 모든 요소를 가져와 파이썬 리스트 형태로 변환
    # (.all()을 사용하면 각 요소가 Locator 객체의 리스트로 반환되어 순회 가능)
    items = page.locator(selector).all()
    print(f"수집된 아이템 개수: {len(items)}")

    # 8. 수집된 딕셔너리 데이터들을 담을 빈 리스트 선언
    total_item = []

    # 9. 각 게임 요소(a 태그)를 하나씩 순회하며 내부 정보 추출
    for item in items:
        # 개별 게임 항목 내부에서 제목 텍스트가 담긴 클래스('.tab_item_title') 탐색
        title_el = item.locator(".tab_item_title")

        # 해당 제목 요소가 실제로 존재할 경우에만 추출 로직 수행
        if title_el.count() > 0:
            # - inner_text(): HTML 태그 속 텍스트 추출
            # - strip(): 앞뒤 불필요한 공백/줄바꿈 제거
            title = title_el.inner_text().strip()

            # - get_attribute("href"): <a> 태그의 href 속성값(상세 페이지 URL) 추출
            link = item.get_attribute("href")

            print(f"게임: {title} | 링크: {link}")

            # 수집된 제목과 링크를 딕셔너리 구조로 리스트에 누적
            total_item.append({"title": title, "link": link})

    # ==============================================================================
    # 데이터 가공 및 파일 저장
    # ==============================================================================

    # 10. 수집된 리스트(total_item)를 Pandas DataFrame(2차원 데이터 구조)으로 변환
    df = pd.DataFrame(total_item)

    # 11. DataFrame을 CSV 파일로 저장
    # - "steam_game_list.csv": 저장할 파일 이름
    # - encoding="utf-8-sig": 엑셀(Excel)에서 한글 깨짐 없이 바로 열리도록 인코딩 지정
    df.to_csv("steam_game_list.csv", encoding="utf-8-sig")

    # ==============================================================================
    # 프로세스 유지 및 안전한 종료
    # ==============================================================================

    # 12. 수집 완료 후 브라우저가 즉시 닫히지 않고 결과를 눈으로 확인하도록 사용자 입력 대기
    print("브라우저가 열렸습니다. 종료하려면 엔터키를 누르세요...")
    input()  # 사용자가 키보드로 Enter를 입력할 때까지 실행을 정지시킴

    # 13. 사용자가 Enter를 누르면 브라우저 세션을 종료하여 자원 정리
    browser.close()