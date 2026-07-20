# Cloudflare provider 연결 안내

ZeroFounder 초기 버전은 Cloudflare 계정, Pages 프로젝트, Worker, D1 데이터베이스를 만들거나 배포하지 않습니다.

Cloudflare Pages 또는 Workers+D1은 `INFRASTRUCTURE_SELECTION`에서 정적 GitHub Pages로 핵심 가설을 검증할 수 없다고 확인된 경우에만 제안됩니다. 창업자는 다음을 직접 검토하고 승인해야 합니다.

1. 계정과 프로젝트 소유권
2. 무료 한도와 향후 비용
3. 처리할 데이터와 보존 정책
4. Pages/Workers secrets 설정
5. D1 binding과 migration
6. 개인정보를 수집하지 않는 API 설계

승인 전에는 Cloudflare 토큰을 저장소에 추가하지 마세요. 승인 후에도 토큰 값은 GitHub Actions secret으로만 관리하고 문서나 로그에 기록하지 않습니다.

