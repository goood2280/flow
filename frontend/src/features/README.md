# Frontend feature boundaries

실제 페이지 구현은 `features/<feature>/`가 소유한다. `pages/*.jsx`는 기존 lazy import와
번들 경계를 보존하기 위한 2줄짜리 compatibility entrypoint만 유지한다.

규칙:

- 새 화면 로직과 feature 전용 component/hook/api는 해당 feature 안에 둔다.
- 공용 UI와 범용 hook/lib만 `components/`, `hooks/`, `lib/`에 둔다.
- feature에서 `pages/`를 역참조하지 않는다. 기능 간 공유가 필요하면 상대 feature의
  명시적 export를 사용하거나 공용 계층으로 승격한다.
- `pages/` 호환 파일과 `pageManifest.jsx`의 경로는 1차 이관 동안 유지한다.

`npm run structure:check`가 호환 entrypoint, feature 대상 파일, 역방향 의존성을 검사한다.
