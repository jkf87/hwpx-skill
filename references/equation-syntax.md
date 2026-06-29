# 한컴 수식 스크립트 문법 (`add-equation`)

`fill_hwpx.py add-equation`은 한글 워드프로세서의 **네이티브 수식 개체**
(<hp:equation>)를 삽입한다. 데스크톱 편집기의 수식 엔진과 같은 것이며,
`--script`로 넘긴 **수식 스크립트** 문자열로 만들어진다. 아래 토큰은 수식
편집기에서 직접 입력한 것과 동일하게 렌더된다.

```bash
# 본문: 기준 문구가 있는 문단 뒤에 새 문단으로 수식 삽입
python3 scripts/fill_hwpx.py add-equation in.hwpx out.hwpx \
  --after "근의 공식:" --script "x = {-b +- sqrt{b^2 -4ac}} over {2a}"

# 표 셀 안에 인라인 수식 (좌표는 analyze/fill --cells와 동일)
python3 scripts/fill_hwpx.py add-equation in.hwpx out.hwpx \
  --table 0 --row 1 --col 1 --script "int _0 ^1 x^2 dx = 1 over 3" --size 1200
```

- `--script` (필수) — 수식 스크립트 (아래 토큰). **대소문자 구분.**
- `--after` 또는 `--table/--row/--col` 중 하나로 위치를 지정.
- `--size` (선택) — 폰트 크기(HWP 단위/100, 1000 ≈ 10pt, 기본 1000).
- 수식은 자기완결 개체라 header.xml/BinData/매니페스트 등록이 필요 없다.
  `--script` 문자열은 수식 개체에 그대로(이스케이프만) 삽입되며, 렌더러는
  편집기에서 입력한 것과 동일하게 처리한다.

## 기본 규칙
- 위 첨자 `a^b` · 아래 첨자 `a_b` · 중괄호로 묶기 `{ }` (예: `e^{-x}`)
- 공백 `~` (더 넓게 `~~`) · 열 정렬 `&` · 행 바꿈 `#`
- 예: `x = {-b +- sqrt{b^2 -4ac}} over {2a}` → 근의 공식

## 구조 템플릿

| 종류 | 토큰 | 비고 |
|---|---|---|
| 첨자 | `^{ }` 위 · `_{ }` 아래 · `{ } LSUP { }` 좌상 · `{ } LSUB { }` 좌하 · `UNDEROVER { } _{ } ^{ }` 위아래 | |
| 분수 | `{ } over { }` | |
| 근호 | `sqrt { }` · `root n of x` (n제곱근) | |
| 큰 연산자 | `sum` `PROD` `COPROD` `INTER` `UNION` `BIGSQCAP` `BIGSQCUP` `BIGOPLUS` `BIGOMINUS` `BIGOTIMES` `BIGODIV` `BIGODOT` `BIGVEE` `BIGWEDGE` `BIGUPLUS` — 모두 `_{ } ^{ }` 한계 가능 | 예: `sum from {i=1} to n` 또는 `sum _{i=1} ^n` |
| 적분 | `int` `dint`(이중) `tint`(삼중) `oint`(선적분) `odint` `otint` — `_{ } ^{ } { }` | |
| 극한 | `lim _{ } { }` · `lim _{x -> 0}` · `lim _{ ->inf}` · `Lim` | `rightarrow`도 → 로 동작 |
| 자동 크기 괄호 | `LEFT ( RIGHT )` · `LEFT [ RIGHT ]` · `LEFT { RIGHT }` · `LEFT < RIGHT >` · `LEFT \| RIGHT \|` · `LEFT DLINE RIGHT DLINE` · `LCEIL RCEIL` · `LFLOOR RFLOOR` · `OVERBRACE { } { }` · `UNDERBRACE { } { }` | 소문자 `left( right)`도 동작 |
| 행렬 | `matrix { & # & }`(괄호 없음) · `pmatrix`(소괄호) · `bmatrix`(대괄호) · `dmatrix`(행렬식) | `&`=열, `#`=행 |
| 경우 분기 | `cases { & # & }` | |
| 세로 쌓기 | `pile { # }` | |
| 긴 나눗셈 | `LONGDIV { } { } { }` | |
| 최소공배수/최대공약수 사다리 | `LADDER { & & # & & }` | |
| 화살표 위/아래 텍스트 | `REL <arrow> { } { }` · `BUILDREL <arrow> { } { }` | arrow = `LRARROW` `lrarrow` `RARROW` `rarrow` `LARROW` `larrow` `EXARROW` |
| 강세 기호 | `vec { }` `dyad { }` `acute { }` `grave { }` `dot { }` `ddot { }` `under { }` `bar { }` `hat { }` `check { }` `arch { }` `tilde { }` `box { }` | 예: `vec{a}` → →a |

## 기호

### 그리스 소문자
`alpha` `beta` `gamma` `delta` `epsilon` `zeta` `eta` `theta` `iota` `kappa` `lambda` `mu` `nu` `xi` `omicron` `pi` `rho` `sigma` `tau` `upsilon` `phi` `chi` `psi` `omega`

### 그리스 대문자
`ALPHA` `BETA` `GAMMA` `DELTA` `EPSILON` `ZETA` `ETA` `THETA` `IOTA` `KAPPA` `LAMBDA` `MU` `NU` `XI` `OMICRON` `PI` `RHO` `SIGMA` `TAU` `UPSILON` `PHI` `CHI` `PSI` `OMEGA`

### 그리스/특수 문자
`ALEPH`(ℵ) `hbar`(ℏ) `imath` `jmath` `ohm` `LITER`(ℓ) `WP`(℘) `IMAG`(ℑ) `ANGSTROM`(Å) `vartheta` `varpi` `varsigma` `varupsilon` `varphi` `varepsilon`

### 집합/합 기호
`SMALLSUM` `SMALLPROD` `SMCOPROD` `SMALLINTER`(∩) `CUP`(∪) `SQCAP` `SQCUP` `OPLUS`(⊕) `OMINUS`(⊖) `OTIMES`(⊗) `ODIV` `ODOT`(⊙) `LOR`(∨) `WEDGE`(∧) `SUBSET`(⊂) `SUPERSET`(⊃) `SUBSETEQ`(⊆) `SUPSETEQ`(⊇) `IN`(∈) `OWNS`(∋) `NOTIN`(∉) `LEQ`(≤) `GEQ`(≥) `SQSUBSET` `SQSUPSET` `SQSUBSETEQ` `SQSUPSETEQ` `<<` `>>` `<<<` `>>>` `PREC`(≺) `SUCC`(≻) `UPLUS`

### 연산자/논리
`+-`(±) `-+`(∓) `TIMES`(×) `DIVIDE`(÷) `CIRC`(∘) `BULLET`(•) `DEG`(°) `AST`(∗) `STAR`(⋆) `BIGCIRC`(○) `EMPTYSET`(∅) `THEREFORE`(∴) `BECAUSE`(∵) `IDENTICAL`(≡) `EXIST`(∃) `!=`(≠) `DOTEQ`(≐) `image` `REIMAGE` `SIM`(∼) `APPROX`(≈) `SIMEQ`(≃) `CONG`(≅) `==` `ASYMP`(≍) `ISO` `DIAMOND`(◇) `DSUM` `FORALL`(∀) `prime`(′) `PARTIAL`(∂) `INF`(∞, 소문자 `inf`도 동작) `LNOT`(¬) `PROPTO`(∝) `XOR` `NABLA`(∇) `DAGGER`(†) `DDAGGER`(‡)

### 화살표
`larrow`(←) `rarrow`(→, `rightarrow`도 동작) `uparrow`(↑) `downarrow`(↓) `LARROW`(⇐) `RARROW`(⇒) `UPARROW`(⇑) `DOWNARROW`(⇓) `udarrow`(↕) `lrarrow`(↔, `<=>`도 ⇔) `UDARROW`(⇕) `LRARROW`(⇔) `NWARROW`(↖) `SEARROW`(↘) `NEARROW`(↗) `SWARROW`(↙) `HOOKLEFT`(↩) `HOOKRIGHT`(↪) `MAPSTO`(↦) `vert`(|) `DLINE`(‖)

### 기타 기호
`CDOTS`(⋯) `LDOTS`(…) `VDOTS`(⋮) `DDOTS`(⋱) `TRIANGLE`(△) `NABLA`(∇) `ANGLE`(∠) `MSANGLE` `SANGLE` `RTANGLE` `VDASH`(⊢) `DASHV`(⊣) `BOT`(⊥) `TOP`(⊤) `MODELS`(⊨) `LAPLACE` `CENTIGRADE`(℃) `FAHRENHEIT`(℉) `LSLANT` `RSLANT` `ATT` `HUND` `THOU` `WELL`(#) `BASE` `BENZENE`

## 예시
```
x = {-b +- sqrt{b^2 -4ac}} over {2a}            → 근의 공식
sum from {i=1} to n i^2 = {n(n+1)(2n+1)} over 6 → 시그마 합
int _0 ^inf e^{-x} dx = GAMMA (1)               → 적분 · 감마
lim _{x rightarrow 0} {sin x} over x = 1        → 극한
A = left [ matrix{1 & 0 # 0 & 1} right ]        → 행렬
root 3 of x ~ oint _0 ^1 x dx                   → 세제곱근 · 선적분
vec{a} cdot bar{b} ~ THEREFORE ~ alpha != OMEGA → 강세 · 기호
```

> `--script` 문자열은 수식 개체에 그대로(XML 이스케이프만) 박히므로,
> `<` `>` `&` 같은 문자도 안전하게 처리된다. 토큰은 대소문자를 구분한다.
