"""
Streamlit Community Cloud 앱은 단순 HTTP 요청(curl 등)만으로는 깨어나지 않는다.
잠자기 화면에 있는 "Yes, get this app back up!" 버튼을 실제로 클릭해야
다시 살아난다. 이 스크립트는 헤드리스 브라우저로 각 앱에 접속해서,
잠자기 화면이 보이면 그 버튼을 클릭해 깨운다.
"""

import sys

from playwright.sync_api import sync_playwright

APP_URLS = [
    "https://iris-monitor.streamlit.app/",
    "https://srome-dashboard.streamlit.app/",
]

WAKE_BUTTON_TEXT = "Yes, get this app back up!"


def wake(page, url: str):
    print(f"[info] 접속: {url}", flush=True)
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"[warn] 접속 실패: {url} ({e})", flush=True)
        return

    try:
        button = page.get_by_text(WAKE_BUTTON_TEXT, exact=False)
        if button.count() > 0:
            print(f"[info] 잠자기 화면 감지 - 깨우기 버튼 클릭: {url}", flush=True)
            button.first.click()
            page.wait_for_timeout(15000)  # 앱이 재시작될 시간을 준다
        else:
            print(f"[info] 이미 깨어있음: {url}", flush=True)
    except Exception as e:
        print(f"[warn] 깨우기 시도 중 오류: {url} ({e})", flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for url in APP_URLS:
            wake(page, url)
        browser.close()


if __name__ == "__main__":
    main()
