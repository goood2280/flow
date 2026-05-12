# GitHub main push

이 문서는 현재 로컬 상태를 GitHub `origin/main`에 바로 올릴 때의 재현 절차다. 목적은 다음 세션에서도 "로컬에 있는 것까지 GitHub main에 푸시" 요청을 같은 방식으로 처리하는 것이다.

## 기준

- 기본 브랜치는 `main`.
- 원격은 `origin`이고 URL은 `https://github.com/goood2280/flow.git`.
- 로컬 작업트리 변경을 사용자가 "지금 로컬한 것"으로 요청하면 코드, 문서, runtime/cache 파일까지 포함해 커밋한다.
- GitHub `main`에 푸시하는 커밋은 항상 `_build_setup.py`로 재생성한 최신 `setup.py`를 포함한다.
- runtime/user data를 임의로 되돌리지 않는다. `git diff --check`가 runtime 기록 파일의 trailing whitespace 때문에 실패하더라도, 요청이 로컬 상태 보존이면 파일 내용을 임의 정리하지 않는다.
- WSL Git에 GitHub credential이 없을 수 있다. 이 경우 Windows Git credential을 쓰는 `git.exe push origin main`이 동작한다.

## 절차

1. 현재 브랜치, 원격, 최근 커밋을 확인한다.

```bash
git status --short --branch
git remote -v
git log --oneline --decorate --max-count=5
```

2. 원격 최신 상태를 가져오고 ahead/behind를 본다.

```bash
git fetch origin
git rev-list --left-right --count main...origin/main
```

출력이 `0 0`이면 로컬/원격 커밋 차이가 없다. 앞 숫자가 0보다 크면 로컬이 앞선 것이고, 뒤 숫자가 0보다 크면 원격이 앞선 것이다. 원격이 앞선 상태에서는 먼저 충돌 가능성을 확인한다.

3. `setup.py`를 재생성하고 버전 출력을 확인한다.

```bash
python3 _build_setup.py
python3 setup.py version
```

이 단계는 source/doc 변경이 작아도 GitHub `main` 푸시 전에는 항상 실행한다. 생성된 `setup.py` diff를 커밋에 포함한다.

4. 변경 내용을 한 번 요약한다.

```bash
git diff --stat
git ls-files --others --exclude-standard
git diff --check
```

`git diff --check` 실패가 소스 코드 공백 오류인지, runtime 기록 파일의 기존 형식 문제인지 구분한다. 사용자가 로컬 상태 그대로 푸시를 요청한 경우 runtime 파일을 임의 정리하지 않는다.

5. 전체 변경을 스테이징하고 커밋한다.

```bash
git add -A
git status --short --branch
git diff --cached --stat
git commit -m "Sync latest local Flow state"
```

커밋 직후 scheduler/cache가 새 runtime 파일을 다시 갱신할 수 있다. `git status --short --branch`를 다시 보고 남은 변경이 있으면 별도 커밋으로 포함한다.

```bash
git status --short --branch
git add -A
git commit -m "Refresh runtime cache state"
```

6. GitHub `main`으로 푸시한다.

```bash
git push origin main
```

WSL에서 아래처럼 HTTPS credential 오류가 나면:

```text
fatal: could not read Username for 'https://github.com': No such device or address
```

Windows Git credential을 사용한다.

```bash
git.exe push origin main
```

7. 푸시 결과를 검증한다.

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log --oneline --decorate --max-count=4
```

`HEAD`와 `origin/main`의 SHA가 같고 status가 `## main...origin/main`이면 완료다.

## 인증 점검

이 환경에서 확인했던 상태:

```bash
gh auth status          # gh가 없을 수 있음
git config --get credential.helper
ssh -T git@github.com  # publickey 실패 가능
which git.exe          # /mnt/c/Program Files/Git/cmd/git.exe
```

토큰 환경변수를 확인해야 할 때는 값이 아니라 이름만 본다.

```bash
env | cut -d= -f1 | rg '^(GH|GITHUB|GIT).*TOKEN|^GH_|^GITHUB_'
```

값을 터미널에 출력하거나 문서에 남기지 않는다.
