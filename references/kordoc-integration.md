# kordoc 통합 검토 노트

이 문서는 [kordoc](https://github.com/chrisryugj/kordoc)의 장점 중
`hwpx-skill`에 이미 반영된 것, 앞으로 반영할 만한 것, 지금은 보류해야 할
것을 정리한다.

## 이미 반영한 장점

### 원본 보존형 HWPX 양식 채우기

`scripts/fill_hwpx.py`는 kordoc의 `fillHwpx`에서 가장 실용적인 핵심을
Python 방식으로 가져왔다.

- 한국 공공서식 라벨을 정규화해 매칭한다.
- 라벨-값 표 셀을 채우되 대상 run의 글자모양을 유지한다.
- HWP→HWPX 변환본에서 빈 값 셀이 `<hp:run/>`처럼 저장되는 경우를 처리한다.
- 체크박스, 괄호 빈칸, 어노테이션 같은 인셀 패턴을 채운다.
- DOM 재직렬화 대신 텍스트 범위만 splice하고, 수정 문단의 stale
  `hp:linesegarray`만 제거한다.
- 변경된 section XML 엔트리만 다시 쓰고, 나머지 ZIP 엔트리는 보존한다.
- 값 존재, 비변경 엔트리 보존, 한컴 열림 위험 신호를 배포 전 검증한다.

이 기능은 "신청서/정산서/강사카드 같은 HWPX 양식을 JSON 값으로 채운다"는
스킬의 실제 사용 흐름과 바로 맞아 떨어진다. Node 런타임이나 kordoc 전체
문서 파서 스택을 기본 의존성으로 추가하지 않아도 효과가 크다.

## 추후 반영 후보

### 양식 필드 타입 추론

kordoc은 양식 필드에 `date`, `phone`, `email`, `amount`, `checkbox`,
`idnum`, `required`, `empty` 같은 스키마 힌트를 붙일 수 있다. Python
버전으로 옮기면 `fill_hwpx.py analyze` 출력이 더 안전해지고, 에이전트가
`values.json`을 만들 때 오입력 가능성을 줄일 수 있다.

권장 출력 형태:

```json
{
  "key": "연락처",
  "type": "phone",
  "required": false,
  "empty": true
}
```

### 인라인 다중 라벨 회귀 테스트

`fill_hwpx.py`도 인라인 라벨을 처리하지만, kordoc에는 `성명:  작성일자:`
처럼 한 문단에 라벨이 여러 개 있는 경우와 URL 콜론 오탐을 막는 테스트가
있다. 휴리스틱을 넓히기 전에 Python 스모크 테스트를 먼저 추가하는 편이
안전하다.

### 선택적 kordoc CLI 브리지

kordoc의 넓은 파서 지원은 문서 입력 단계에서 가치가 있다.

- HWP 3.x
- HWP 5.x
- HWPML
- PDF
- XLS/XLSX
- DOCX

다만 기본 의존성이 아니라 선택 기능이어야 한다. 예를 들어 다음처럼
`npx kordoc` 존재 여부를 확인하고 없으면 친절히 실패하는 래퍼를 둘 수 있다.

```bash
python3 scripts/kordoc_extract.py input.docx --format markdown
```

핵심 HWPX 생성·편집 경로는 계속 로컬 Python 스크립트를 기본으로 둔다.

### 문서 diff와 라운드트립 패치

kordoc의 document diff, `patchHwp`, `patchHwpx` API도 유용하지만 현재
스킬 경계보다 크다. "두 한글 문서 비교" 또는 "추출한 Markdown을 수정한 뒤
원본에 패치" 같은 독립 워크플로우를 먼저 정의한 뒤 검토한다.

## 지금은 보류할 것

- kordoc 전체 vendoring: Python-first HWPX 스킬에 비해 범위가 너무 넓다.
- MCP 서버 설치 흐름: OpenClaw/Codex에서는 로컬 스크립트 직접 호출이 이미
  자연스럽다.
- PDF/DOCX/XLS 파서 내장: 입력 변환에는 유용하지만 HWPX 생성·편집 핵심
  범위 밖이다.
- HWP 바이너리 in-place 패치: 가치가 크지만 위험도도 크다. 실패 게이트와
  테스트가 충분히 정의되기 전에는 현재의 HWP→HWPX 변환 경로를 유지한다.
