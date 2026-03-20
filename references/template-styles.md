# 템플릿별 스타일 ID 맵

> **⚠️ 중요: 템플릿 간 스타일 ID는 호환되지 않는다.**
> government의 charPr 81을 report 템플릿에 쓰면 깨진다.
> 반드시 사용 중인 템플릿의 ID만 참조할 것.

## base (기본) — charPr 7개, paraPr 20개, borderFill 2개

| ID | 유형 | 설명 |
|----|------|------|
| charPr 0 | 글자 | 10pt 함초롬바탕 (기본) |
| charPr 1 | 글자 | 10pt 함초롬돋움 (쪽번호) |
| charPr 2 | 글자 | 9pt 함초롬돋움 (머리말) |
| charPr 3 | 글자 | 9pt 함초롬바탕 (각주/미주) |
| charPr 4 | 글자 | 9pt 함초롬돋움 (메모) |
| charPr 5 | 글자 | 16pt 함초롬돋움, #2E74B5 (차례 제목) |
| charPr 6 | 글자 | 11pt 함초롬돋움 (차례 항목) |
| paraPr 0 | 문단 | JUSTIFY, 160% 줄간격 |

> **주의**: charPr 3은 "16pt 제목"이 아니라 **9pt 각주용**이다.
> base에서 제목용으로 쓸 수 있는 것은 charPr 5 (16pt, 파란색)뿐.

## report (보고서) — charPr 16개, paraPr 28개, borderFill 6개

| ID | 유형 | 설명 |
|----|------|------|
| charPr 0~6 | 글자 | base와 동일 |
| charPr 7 | 글자 | 20pt 볼드 바탕 (문서 제목) |
| charPr 8 | 글자 | 14pt 볼드 바탕 (소제목) |
| charPr 9 | 글자 | 10pt 볼드 바탕 (표 헤더) |
| charPr 10 | 글자 | 10pt 볼드+밑줄 바탕 (강조) |
| charPr 11 | 글자 | 9pt 바탕 (소형/각주) |
| charPr 12 | 글자 | 16pt 볼드 바탕 (1줄 제목) |
| charPr 13 | 글자 | 12pt 볼드 돋움 (섹션 헤더) |
| charPr 14 | 글자 | 26pt 볼드 돋움 (대형 제목) |
| charPr 15 | 글자 | 24pt 볼드+밑줄 돋움 (대형 제목) |
| paraPr 20 | 문단 | CENTER, 160% |
| paraPr 21 | 문단 | CENTER, 130% (표 셀) |
| paraPr 22 | 문단 | JUSTIFY, 130% (표 셀) |
| paraPr 23 | 문단 | RIGHT, 160% |
| paraPr 24~26 | 문단 | 들여쓰기 600/1200/1800 |
| paraPr 27 | 문단 | 상하단 테두리선 (섹션 헤더) |
| borderFill 3 | 테두리 | SOLID 0.12mm 4면 (표 기본) |
| borderFill 4 | 테두리 | SOLID + #DAEEF3 배경 (표 헤더) |
| borderFill 5 | 테두리 | 상단 0.4mm + 하단 0.12mm (구분선) |
| borderFill 6 | 테두리 | SOLID + #D9D9D9 배경 (회색 셀) |

## gonmun (공문서)

| ID | 유형 | 설명 |
|----|------|------|
| charPr 7 | 글자 | 22pt 볼드 바탕 (기관명/제목) |
| charPr 8 | 글자 | 16pt 볼드 바탕 (서명자) |
| charPr 10 | 글자 | 10pt 볼드 (표 헤더) |
| borderFill 4 | 테두리 | SOLID + #D6DCE4 배경 |

## minutes (회의록)

| ID | 유형 | 설명 |
|----|------|------|
| charPr 7 | 글자 | 18pt 볼드 (제목) |
| charPr 8 | 글자 | 12pt 볼드 (섹션 라벨) |
| borderFill 4 | 테두리 | SOLID + #E2EFDA 배경 |

## proposal (제안서)

| ID | 유형 | 설명 |
|----|------|------|
| charPr 10 | 글자 | 14pt 볼드 흰색 돋움 (대항목) |
| charPr 11 | 글자 | 11pt 볼드 흰색 돋움 (소항목) |
| borderFill 5 | 테두리 | #7B8B3D 녹색 배경 |
| borderFill 7 | 테두리 | #4472C4 파란 배경 |

## government (관공서/교육청) — ★ 컬러 섹션 바 + 표지 배너

> **실제 관공서 문서에서 추출한 160+ charPr, 100+ paraPr, 50+ borderFill 포함.**
> **`--header ${CLAUDE_SKILL_DIR}/templates/government/header.xml`로 지정.**

| ID | 유형 | 설명 |
|----|------|------|
| charPr 18 | 글자 | 10pt 볼드 (본문 마커 "가.", "나.") |
| charPr 38 | 글자 | 10pt 기본 (본문 내용) |
| charPr 60 | 글자 | 날짜 텍스트 |
| charPr 62 | 글자 | 부제 텍스트 |
| charPr 81 | 글자 | 흰색 볼드 (섹션 바 번호) |
| charPr 82 | 글자 | 섹션 바 간격 셀 |
| charPr 83 | 글자 | 볼드 (섹션 바 제목) |
| charPr 144 | 글자 | 표지 배너 제목 |
| **보조 charPr** | | |
| charPr 0 | 글자 | 기본 (이미지 run 등) |
| charPr 25 | 글자 | 첫 문단 secPr run |
| charPr 41 | 글자 | 빈 줄 / 페이지 넘김 |
| charPr 42 | 글자 | 표 얇은 셀 (배너 컬러 바) |
| paraPr 0 | 문단 | 기본 |
| paraPr 1 | 문단 | CENTER (날짜) |
| paraPr 2 | 문단 | 표 셀 기본 |
| paraPr 4 | 문단 | 본문 JUSTIFY |
| paraPr 18 | 문단 | 빈 줄 / 페이지 넘김 |
| paraPr 19 | 문단 | 이미지 문단 |
| paraPr 20 | 문단 | 표지 제목 CENTER |
| paraPr 21 | 문단 | 섹션 바 번호 CENTER |
| paraPr 40 | 문단 | 첫 문단 (secPr 포함) |
| paraPr 52 | 문단 | 부제 |
| borderFill 4 | 테두리 | 표 기본 테두리 |
| borderFill 8 | 테두리 | 노랑 (표지 바) |
| borderFill 9 | 테두리 | 초록 (표지 바) |
| borderFill 10 | 테두리 | 파랑 (표지 바) |
| borderFill 11 | 테두리 | 빨강 (표지 바) |
| borderFill 12 | 테두리 | 하늘색 (섹션 바 제목) |
| borderFill 13 | 테두리 | 회색 (섹션 바 간격) |
| borderFill 14 | 테두리 | 파랑 (섹션 바 번호) |
| borderFill 15 | 테두리 | 회색 (표지 배너 제목) |

## 마크다운 → HWPX 매핑 (report 기준)

| 마크다운 요소 | charPrIDRef | paraPrIDRef |
|-------------|-------------|-------------|
| `# 제목` | 7 | 20 |
| `## 섹션` | 8 | 0 |
| `### 소제목` | 13 | 27 |
| 일반 텍스트 | 0 | 0 |
| `**볼드**` | 9 | 0 |
| `> 인용` | 11 | 24 |
| `- 목록` | 0 | 24/25/26 |
| 표 헤더 | 9 | 21 |
| 표 셀 | 0 | 22 |
| 빈 줄 | 0 | 0 |
