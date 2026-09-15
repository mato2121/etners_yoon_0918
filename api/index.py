import os
import sys

# 프로젝트 루트(app.py가 있는 상위 폴더)를 import 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (Vercel Python 런타임이 이 WSGI app 객체를 실행함)
