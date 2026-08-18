# v1.9.0 — Windows 한컴 엔진 HWP→HWPX 고속·일괄 변환 (2026-08-18)

Windows에 한컴오피스 한글이 설치된 환경에서 실제 한글 Automation COM 엔진으로
HWP를 HWPX로 빠르게 변환하는 경로를 추가했습니다. 한 번 생성한 한글 프로세스를
배치 전체에서 재사용하므로 폴더 단위 변환이 빠르고, 한컴 엔진을 사용할 수 없는
환경에서는 기존 `rhwp` WASM 변환기로 폴백합니다.

## 주요 변경

- `scripts/convert_hwp_hancom.ps1`을 추가했습니다. 파일·폴더 입력,
  `-OutputDirectory`, `-Recurse`, `-Overwrite`, `-Visible`을 지원합니다.
- 폴더 입력에서 `.hwp` 확장자만 정확히 선택해 `.hwpx`를 다시 변환하지 않습니다.
- `HWPFrame.HwpObject`를 파일마다 재실행하지 않고 전체 배치에서 재사용합니다.
- 등록된 `HwpAutomation` 파일 경로 보안 모듈을 자동으로 찾아
  `FilePathCheckDLL`로 등록합니다.
- 결과를 대상 폴더의 임시 HWPX에 먼저 저장하고 성공 후 교체해, 실패 시 기존
  출력 파일을 보존합니다.
- 여러 입력이 하나의 출력 폴더에서 같은 파일명으로 충돌하면 변환 전에 중단합니다.

## 스킬 동작 변경

- Windows에서는 한컴 COM 변환기를 우선 사용하고, COM 엔진이나 보안 모듈을
  사용할 수 없으면 내장 `convert_hwp.py` 경로로 폴백합니다.
- 사용자가 HWP의 읽기·추출·편집·양식 채우기를 요청하면 원본을 보존한 별도
  HWPX를 만든 뒤 기존 E/C/J/F 워크플로우를 계속합니다.
- 사용자가 HWP 형식 유지나 변환 금지를 명시하면 자동 변환하지 않습니다.
- COM 변환 결과에도 `validate.py`, `fill_hwpx.py check --strict`, 가능한 경우
  `validate.py --hancom` 검증을 적용하도록 문서화했습니다.
- 기존 Node.js 18+ `rhwp` WASM CLI와 Python API는 변경 없이 유지됩니다.

## 호환성

- 새 PowerShell 변환기는 Windows와 설치된 한컴오피스 한글이 필요합니다.
- 파일 경로 보안 경고 없이 자동화하려면
  `HKCU\Software\HNC\HwpAutomation\Modules`에 호환 보안 모듈이 등록되어야 합니다.
- 한컴 COM 요구 사항을 만족하지 않는 환경에서는 v1.8.0과 같은 vendored
  `@rhwp/core` 0.7.10 변환 경로를 계속 사용할 수 있습니다.

## 검증

- PowerShell 구문과 회귀 테스트에서 정확한 확장자 필터, 새 출력과 기존 출력의
  원자 교체, 임시 파일 정리를 검증했습니다.
- 실제 한컴오피스 11.0.0.8362 엔진으로 HWP 6개를 한 프로세스에서 4.146초에
  변환했습니다. 생성된 6개 HWPX는 모두 구조 검사, strict 게이트, 한컴 재열기를
  통과했습니다.
- 기존 rhwp 변환 테스트 9개와 Python 테스트 스크립트 20개 중 19개가 통과했습니다.
  변경하지 않은 `test_report_placeholder_hook.py`는 공백이 있는 Windows 절대 경로를
  정규식으로 추출하지 못하는 기존 플랫폼 한계로 5/7만 통과했습니다.

---

# v1.8.0 — K-Teacher 활동지 HTML→HWPX·라운드 카드 디자인 (2026-07-22)

K-Teacher 학생 활동지의 카드형 디자인을 편집 가능한 HWPX로 옮기는
전용 변환 워크플로우를 추가했습니다. HTML을 디자인 계획 XML로 정규화한 뒤
네이티브 HWPX 표·문단·둥근 도형으로 생성하므로, 한글에서 내용과 표를 계속
편집할 수 있습니다.

## 주요 변경

- `scripts/html2hwpx.py`를 추가해 K-Teacher 활동지 HTML을
  `design-plan.xml → section0.xml → HWPX` 순서로 변환합니다.
- `.doc-header`, `section.block`, `student_task`, `source_card`, `answer_box`,
  `exit_ticket`, 자료표와 쪽 나누기를 결정론적으로 매핑합니다.
- 학습 목표, STEP 과제, 자료 카드, 출구표를 네이티브 `hp:rect` 기반
  라운드 카드로 생성하고, 표와 답안 영역은 안정적인 HWPX 표 구조를 유지합니다.
- `fill_hwpx.py insert-shape`와 `insert-textbox`에 `--rounding 0~100` 옵션을
  추가했습니다. 값은 OWPML `hp:rect@ratio`로 직접 기록됩니다.
- 스킬 트리거에 `HTML을 HWPX로`, `K-Teacher 스타일`, `컬러 활동지`를 추가하고
  워크플로우 K와 HTML 매핑 가이드를 문서화했습니다.

## 디자인·호환성

- 디자인 토큰과 구성 원칙을 `DESIGN.md`에 정리했습니다.
- 브라우저 CSS 전체를 복제하는 범용 변환기가 아니라, 지원하는 활동지 의미
  구조를 HWPX에 안정적으로 대응하는 제한형 변환기입니다.
- 생성 후 네임스페이스 정리, 줄배치 캐시 제거, 구조·레이아웃 검증을 자동으로
  수행합니다.
- `--rounding`의 기본값은 0이므로 기존 도형·글상자 생성 결과와 호환됩니다.

## 검증

- HTML 파싱, 디자인 팔레트, 라운드 도형 비율, 종단 간 HWPX 생성을 검증하는
  테스트와 K-Teacher HTML fixture를 추가했습니다.
- 도형 회귀 테스트 12개와 HTML→HWPX 테스트 2개가 모두 통과했습니다.
- 설치된 스킬에서 생성한 결과가 `validate.py --layout`의 모든 구조 검사를
  통과했으며, XML에 `ratio="24"` 라운드 도형이 기록되는 것을 확인했습니다.
- 생성한 2쪽 예제 HWPX를 한컴오피스 한글에서 열고 PDF로 내보내 시각적으로
  확인했습니다.

## 라이선스·출처

- 카드형 시각 패턴과 팔레트는 MIT 라이선스의
  [`pblsketch/k-teacher-skills`](https://github.com/pblsketch/k-teacher-skills)를
  참고했습니다.
- `THIRD_PARTY_NOTICES.md`와 원문 라이선스 사본을 추가해 출처와 적용 범위를
  명시했습니다.

---

# v1.7.0 — HWP→HWPX 변환 엔진·레이아웃 보존 개선 (2026-07-11)

기존 Python 변환기와 실행 중 의존성 설치·Git clone 경로를 제거하고,
`claw-hwp`에서 검증된 `@rhwp/core` 0.7.10 WASM 런타임을 저장소에 고정했습니다.

## 주요 변경

- HWP→HWPX 변환 엔진을 vendored `rhwp` WASM 런타임으로 교체했습니다.
- 실행 중 패키지 설치나 외부 저장소 clone 없이 Node.js 18+에서 변환합니다.
- 임시 경로에서 변환·정규화·검증을 모두 통과한 뒤 결과 파일을 원자적으로 교체합니다.
- 이미지 매니페스트의 embedded 표시, 0으로 기록된 이미지 크기, 미리보기 텍스트를 보정합니다.
- 원본 HWP의 섹션별 용지 크기와 상하좌우·머리말·꼬리말·제본 여백을 HWPX에 보존합니다.
- 유효한 `hp:linesegarray`를 유지해 원본의 줄 간격과 표 배치를 최대한 보존합니다.
- 표 앞의 whitespace-only 텍스트가 표를 중복 배치시키는 경우 해당 공백만 제거합니다.

## 안정성·호환성

- 입력 HWP와 출력 HWPX가 같은 경로면 변환을 거부해 원본 덮어쓰기를 방지합니다.
- 실패 시 기존 출력 파일을 보존하고 임시 파일을 남기지 않습니다.
- 기존 `--output=...`, `--keep-char-borders` CLI와 Python 함수 인자를 계속 허용합니다.
- `--info --json`은 문서 버전, 섹션·페이지·문단 수, 검증 경고 수를 제공합니다.
- `title`, `author`, `subject`, `keywords`처럼 현재 런타임이 제공하지 않는 메타데이터는
  추정하지 않고 `null`로 반환합니다.

## 검증

- 공개 HWP fixture 기반 변환 회귀 테스트 9개를 추가했습니다.
- 저장소의 전체 19개 테스트 스크립트에서 `493 passed`, 실패 0을 확인했습니다.
- 표 셀 줄바꿈, 원본 용지·여백, 이미지·미리보기 패치, 실패 시 원자성,
  기존 CLI/API 호환성, Node.js ESM 경계를 검증합니다.
- 실제 4쪽 강사비 신청서와 1쪽 이력서에서 변환 전후 페이지 수와 용지·여백이
  각각 `4→4`, `1→1`로 일치함을 확인했습니다.
- 생성된 결과는 `validate.py`와 `fill_hwpx.py check --strict`를 모두 통과했습니다.

## 알려진 한계

- HWP와 HWPX의 표현 차이로 복잡한 도형, OLE 객체, 일부 표 음영은 달라질 수 있습니다.
- 구조 검증은 한컴에서의 픽셀 단위 시각 동일성을 보장하지 않으므로 중요한 문서는
  원본과 함께 보관하고 한컴에서 최종 확인해야 합니다.
