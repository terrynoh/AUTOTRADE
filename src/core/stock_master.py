"""종목 마스터 모듈.

config/stock_master.json 을 로드하여 종목코드 ↔ 종목명 양방향
lookup 을 제공한다.

용도:
- screener: KIS API 응답의 name 필드가 비어있을 때 fallback
- notifier: 텔레그램 /target 명령에서 종목명 → 종목코드 변환
- main.py: 종목 마스터 로드 시점에 인스턴스 생성 후 주입
"""

import json
from pathlib import Path
from typing import Optional


class StockMaster:
    """종목 마스터. {code: name} dict 양방향 캐시."""

    def __init__(self, json_path: Path):
        """
        Args:
            json_path: config/stock_master.json 경로
        """
        self._code_to_name: dict[str, str] = {}
        self._name_to_code: dict[str, str] = {}
        self._load(json_path)

    def _load(self, json_path: Path) -> None:
        """JSON 파일 로드. 파일 없으면 빈 dict 로 초기화 (에러 안 냄)."""
        if not json_path.exists():
            return
        master = json.loads(json_path.read_text(encoding="utf-8"))
        for code, name in master.items():
            self._code_to_name[code] = name
            self._name_to_code[name.upper()] = code

    def lookup_name(self, code: str, default: str = "") -> str:
        """종목코드 → 종목명. 없으면 default 반환.

        용도: KIS API 응답의 name 이 빈 문자열일 때 fallback.

        Args:
            code: 6자리 종목코드
            default: 미매칭 시 반환값 (보통 code 자체 또는 빈 문자열)
        """
        return self._code_to_name.get(code, default)

    def lookup_code(self, name_or_code: str) -> Optional[str]:
        """종목명 또는 종목코드 → 종목코드.

        - 6자리 숫자가 입력되면: 종목 마스터에 있으면 그대로 반환, 없으면 None
        - 그 외 입력은: 종목명으로 lookup (대소문자 무시)

        용도: 텔레그램 /target 명령의 종목명 입력 처리.

        Args:
            name_or_code: 사용자 입력 (예: "삼성전자" 또는 "005930")

        Returns:
            매칭 시 6자리 종목코드, 미매칭 시 None
        """
        s = name_or_code.strip()
        if len(s) == 6 and s.isdigit():
            return s if s in self._code_to_name else None
        return self._name_to_code.get(s.upper())

    def __len__(self) -> int:
        """로드된 종목 수."""
        return len(self._code_to_name)
