# Game Control MCP

Codex가 Windows 게임을 실행하고 게임 창을 보고 입력을 보낼 수 있게 해 주는 로컬 MCP 서버입니다. 실행 파일 경로를 지정해 게임을 시작하고 창을 자동 선택하거나, 이미 실행 중인 게임 창을 직접 선택해 스크린샷·마우스·터치·키보드 입력을 사용할 수 있습니다.

## 요구 사항

- Windows 10 또는 11
- Python 3.11 이상
- [uv](https://docs.astral.sh/uv/)
- Codex 앱 또는 Codex CLI

## 설치와 Codex 연결

먼저 PowerShell에서 프로젝트 폴더로 이동해 의존성을 설치합니다.

```powershell
cd E:\_work\mcp\game_control
uv sync
```

Codex 설정 파일 `%USERPROFILE%\.codex\config.toml`에 아래 항목을 추가합니다. 경로를 다른 곳에 체크아웃했다면 `--directory` 뒤의 경로를 실제 프로젝트 폴더로 바꾸세요.

```toml
[mcp_servers.game_control]
command = "uv"
args = ["--directory", "E:\\_work\\mcp\\game_control", "run", "python", "-m", "game_control.server"]
```

Codex를 완전히 종료하고 다시 시작합니다. MCP는 `stdio` 연결로 동작하므로 **Codex가 설정을 읽고 서버 프로세스를 직접 시작해야 합니다.** `run_server.bat`을 더블클릭해 실행하는 것만으로는 Codex에 연결되지 않습니다. 배치 파일은 의존성을 맞추고 서버를 직접 실행할 때 사용합니다.

## 첫 사용

1. 실행 파일을 바로 제어하려면 Codex에 경로를 주고 `launch_game`으로 실행해 달라고 요청합니다. 게임 창이 나타나면 자동으로 선택됩니다.
2. 이미 실행 중인 게임이라면 “게임 창 목록을 보여줘”라고 요청해 `list_windows` 결과를 확인하고, `select_window`로 대상을 지정합니다.
3. `get_screenshot`으로 화면을 받아 확인한 다음 클릭, 탭, 키 입력을 요청합니다.

예를 들면 이렇게 요청할 수 있습니다.

> `E:\Games\Example\main.exe`를 실행하고 화면을 보여줘. 화면 가운데를 클릭해 줘.

`launch_game`은 전체 실행 파일 경로를 받으며, 게임 창을 최대 60초 동안 기다립니다. 필요하면 `startup_timeout_seconds`를 1~180초로 지정할 수 있습니다. 시작 프로그램이 별도 프로세스로 게임을 여는 경우처럼 실행한 프로세스와 게임 창의 프로세스 ID가 다르면 자동 선택되지 않을 수 있습니다. 이때는 `list_windows`와 `select_window`를 사용하세요.

창 제목이 중복되거나 모호하면 `list_windows`에 나온 제목과 `window_id`를 함께 지정하세요. 선택한 창이 닫히면 다시 목록을 조회해 선택해야 합니다.

`stop_game`은 현재 선택된 창의 프로세스 트리를 종료합니다. MCP 연결이 끝나면 그 연결에서 `launch_game`으로 시작한 게임도 자동 종료됩니다. 이미 실행 중인 앱을 `select_window`로 골랐다면 연결 종료만으로는 종료하지 않으며, `stop_game`을 호출해야 합니다. 이 서버는 `stdio` 연결마다 별도 프로세스로 실행되므로 각 연결의 선택 창과 실행한 게임 추적은 서로 분리됩니다.

## 제공 도구

| 도구 | 동작 |
| --- | --- |
| `launch_game(executable_path, startup_timeout_seconds)` | 실행 파일을 시작하고 해당 프로세스의 창이 나타나면 자동 선택 |
| `stop_game()` | 현재 선택한 앱과 그 자식 프로세스를 종료 |
| `list_windows` | 표시 중인 창의 ID, 제목, 프로세스 ID, 크기 조회 |
| `select_window(window_id)` | 이후 입력과 캡처에 사용할 창 선택 |
| `get_screenshot` | 선택한 창의 화면을 JPEG 이미지로 반환 |
| `click(x, y, button, input_mode)` | 화면 포인터를 움직여 클릭하거나, 선택된 창에 마우스 메시지를 보내 클릭 |
| `tap(x, y)` | 이미지 픽셀 위치에 Windows 터치 탭 입력 |
| `swipe(start_x, start_y, end_x, end_y, duration_ms, steps)` | 이미지 픽셀 위치 사이에 손가락을 누른 채 이동하는 Windows 터치 스와이프 입력 |
| `move_mouse(x, y)` | 이미지 픽셀 위치로 마우스 이동 |
| `press_key(key, duration_ms, input_mode)` | 키 하나를 시스템 입력 또는 창 메시지로 눌렀다가 놓기 |
| `press_keys(keys, interval_ms, duration_ms, input_mode)` | 여러 키를 시스템 입력 또는 창 메시지로 순서대로 입력 |
| `type_text(text, input_mode)` | 시스템 입력 또는 `WM_CHAR` 창 메시지로 텍스트 입력 |
| `wait(seconds)` | 게임 전환이나 애니메이션을 기다림 |

### 스크린샷과 좌표

스크린샷은 JPEG 품질 80이며, 종횡비를 유지한 채 긴 변을 최대 1280픽셀로 줄입니다. 도구 결과에 이미지와 이미지의 픽셀 크기가 함께 표시됩니다.

`click`, `tap`, `move_mouse`, `swipe` 좌표는 **가장 최근 `get_screenshot` 결과 이미지의 픽셀 좌표**입니다. `(0, 0)`은 이미지 왼쪽 위이며, 예를 들어 이미지가 `1280 × 720`이면 가운데는 대략 `(640, 360)`입니다. 서버가 이 값을 실제 창 크기로 환산합니다. 새 화면에서 위치를 정할 때는 최신 스크린샷을 먼저 받으세요. 좌표는 이미지 범위 안의 정수여야 합니다.

`swipe`는 시작점에서 끝점까지 손가락을 누른 상태로 이동합니다. 기본값은 350ms, 12단계이며, `duration_ms`는 50~5000, `steps`는 2~100 범위에서 지정합니다. 가로로 넘기는 화면은 오른쪽에서 왼쪽으로 스와이프하는 식으로 조작할 수 있습니다.

```text
swipe(start_x=1050, start_y=350, end_x=300, end_y=350)
```

### 포인터를 움직이지 않는 창 메시지 입력

`click`, `press_key`, `press_keys`, `type_text`는 `input_mode`를 지정할 수 있습니다.

- 기본값 `system`은 기존처럼 전역 시스템 마우스/키보드 입력을 보내며, 선택 창에 포커스를 줍니다. `click`은 포인터를 해당 좌표로 이동합니다.
- `window_message`는 선택된 창 핸들에 `WM_MOUSE*`, `WM_KEY*`, 또는 `WM_CHAR` 메시지를 직접 보냅니다. 클릭 좌표는 해당 창의 클라이언트 좌표로 환산하며 포인터를 움직이거나 창을 활성화하지 않습니다.

예를 들어 포인터 이동 없이 클릭하려면 `click(x=500, y=300, input_mode="window_message")`를 사용합니다. 배경 창에 키 입력을 시도할 때는 `press_key(key="space", input_mode="window_message")`처럼 지정합니다.

창 메시지는 실제 하드웨어 입력이 아닙니다. 일반 Windows 메시지를 처리하는 프로그램에서만 동작하며, Raw Input을 직접 읽는 게임이나 특정 엔진은 이를 무시할 수 있습니다. 이 경우 기본 `system` 입력을 사용하세요.

### 키보드 입력 시퀀스

`press_keys`에 키 이름 목록을 전달하면 순서대로 눌렀다 놓습니다.

```text
keys = ["w", "w", "space"]
interval_ms = 300
duration_ms = 80
```

위 예시는 W, W, 스페이스를 입력합니다. 기본적으로 각 키는 80ms 동안 눌리고, 다음 키 입력은 앞 키 입력 시작 시점에서 300ms 뒤에 시작합니다. 기본 간격과 누르는 시간은 각각 300ms, 80ms이며 호출마다 바꿀 수 있습니다. 한 번에 1~100개 키를 보낼 수 있습니다.

`press_key`와 `press_keys`는 문자·숫자와 `space`, `enter`, `esc`, 방향키, `shift`, `ctrl`, `alt`, `f1`~`f12` 같은 키 이름을 지원합니다. `type_text`는 채팅이나 텍스트 입력용입니다.

## 실행과 점검

Codex 설정에 등록하지 않고 서버만 직접 시작하려면 프로젝트 폴더에서 다음 명령을 실행하거나 `run_server.bat`을 사용합니다.

```powershell
uv run python -m game_control.server
```

이 방식은 MCP 클라이언트가 `stdio`로 연결할 때 쓰는 실행 방식입니다. 일반 터미널에서 실행하면 서버가 입력을 기다리는 것처럼 보일 수 있습니다. Codex에서 사용하려면 위의 Codex 설정을 사용하세요.

Codex에서 도구가 보이지 않을 때 확인할 점:

1. `%USERPROFILE%\.codex\config.toml`에 `[mcp_servers.game_control]` 항목이 있는지 확인합니다.
2. `command`에 지정한 `uv`를 PowerShell에서 실행할 수 있는지 확인합니다.
3. `--directory` 경로가 `pyproject.toml`이 있는 프로젝트 폴더인지 확인합니다.
4. 설정을 바꾼 뒤 Codex를 완전히 재시작합니다.

## 제한 사항

이 서버는 Windows 데스크톱 창 캡처와 일반 키보드·마우스 입력, Windows 터치 입력을 사용합니다. 게임에 따라 독점 전체 화면, 안티치트 또는 Raw Input 설정이 캡처나 입력을 막을 수 있습니다. 이 경우 게임을 창 모드 또는 무테 창 모드로 실행해 보세요.
