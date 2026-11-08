# Delta Review

델타포스 익스트랙션 녹화 파일을 **당시 이용 가능했던 정보**로 복기하는 로컬 데스크톱 앱입니다. 현재 버전은 **1단계 v0.1**입니다. Python/PySide6로 실행하며 서버·계정·외부 AI 키가 필요하지 않습니다.

실제 게임 영상이나 학습된 게임 전용 모델 없이 적·실수·최적 감도를 알아냈다고 표시하지 않습니다. 화면 변화 탐색, 로컬 전사 연결, 수동 근거 편집, 시간 제한을 적용한 보고서, 원본 구간 재생이 구현돼 있습니다. 실제 구현/부분 구현/미구현 구분은 [PROJECT_STATUS.md](PROJECT_STATUS.md)를 참고하세요.

## Windows 11에서 실행

대상은 **Windows 11 x64, Python 3.12**입니다. Linux에서 개발·검증했으며 **Windows 실행은 아직 직접 검증하지 않았습니다**.

1. [Python 공식 다운로드](https://www.python.org/downloads/windows/)에서 Python 3.12 x64와 `py` 런처를 설치합니다.
2. [FFmpeg 공식 다운로드 안내](https://ffmpeg.org/download.html#build-windows)에 연결된 Windows 빌드를 설치합니다. `ffmpeg.exe`, `ffprobe.exe`가 있는 `bin`을 PATH에 추가하고 PowerShell을 다시 엽니다. 이미 신뢰하는 패키지 관리자가 있다면 `winget install --id Gyan.FFmpeg -e`도 선택지입니다. 설치 파일 서명·공급처를 확인하고 검증을 끄지 마세요.
3. 프로젝트 폴더에서 실행합니다. PowerShell 스크립트 실행 정책을 바꾸고 싶지 않으면 아래 개별 Python 명령을 사용하세요.

```powershell
.\scripts\setup-windows.ps1
.\scripts\run-windows.ps1
```

개별 명령:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m delta_review doctor
.\.venv\Scripts\python.exe -m delta_review
```

기본 저장 위치는 `%LOCALAPPDATA%\DeltaReview`입니다. 다른 드라이브를 쓰려면:

```powershell
.\scripts\run-windows.ps1 -DataDir 'D:\DeltaReviewData'
```

`requirements-lock.txt`는 이번 개발 환경의 정확한 의존성 버전입니다. 배포용 EXE/설치 프로그램은 아직 만들지 않았습니다. [Windows 수동 검증 절차](docs/WINDOWS_CHECKLIST.md)를 실행한 뒤 실사용 범위를 넓혀 주세요.

## 첫 복기

1. **경기 보관함**에서 목표(생존·탈출, 임무, 전리품, 아군 지원)를 선택하고 녹화를 마친 MP4/MKV를 가져옵니다. MOV/WebM도 허용합니다. 원본은 그대로 두고 앱 보관함에 전체 사본을 저장합니다.
2. 가져오기가 끝나면 분석 큐에 한 번 등록합니다. 현재 자동 영상 분석은 약 2Hz 화면 변화 후보 탐색입니다. 적·피격·사망을 판정하는 모델이 아닙니다.
3. **설정·저장**에서 로컬 한국어 음성 모델을 지정합니다. 모델이 없으면 보고서에 **전사 미실행**을 표시합니다. 이 경우 **SRT 가져오기** 또는 **수동 사건 추가 → 음성 전사**를 이용할 수 있습니다. 소리가 없는 영상은 시각·수동 근거만 분석합니다.
4. **경기 상세**에서 음성 트랙을 선택하고 원본과 자막을 확인합니다. 모든 화자는 기본적으로 미확인입니다. 전사 목록을 더블클릭해 시각·내용·화자·정정 대상·확인 여부를 수정합니다. 잘못된 인식은 확인 체크를 해제하세요.
5. **분석 / 재시도**로 변경된 근거에 맞춰 보고서를 다시 만듭니다. 수정 전 보고서는 숨겨지고 재분석 안내를 표시합니다. 사건 또는 보고서 근거 링크를 누르면 원본 해당 구간을 재생합니다.
6. **감도 관찰**에서 설정을 프로필로 저장하고 교전 구간을 수동 기록합니다. 현재 자동 표적 추적·감도 추정은 없으며 수치 후보는 **자료 부족**으로 보류합니다.

자동 ASR/SRT는 검토 전까지 전술 대안의 근거로 사용하지 않습니다. 확인된 발화도 발화 내용의 진실이나 실제 청취·이해를 뜻하지 않습니다. 조건부 대안은 사용자가 검토할 일반 비교 틀이며 게임 장면에 검증된 최적 행동 판정이 아닙니다.

## 로컬 한국어 전사

`faster-whisper`는 설치되지만 모델 가중치는 포함되지 않습니다. 사용자가 신뢰하는 경로로 내려받은 **다국어 CTranslate2 Whisper 모델 폴더**를 설정 화면에 지정하세요. `model.bin`, `config.json`, `tokenizer.json`이 필요합니다. 영어 전용 `.en` 모델은 한국어용으로 쓰지 마세요.

- 공식 구현·변환 안내: https://github.com/SYSTRAN/faster-whisper
- 기본 실행: CPU, int8, CPU 스레드 2개, `language=ko`, VAD, 문장 간 추정 문맥 비활성화.
- 앱은 모델 이름을 통한 자동 다운로드를 하지 않고 `local_files_only=True`로 엽니다.
- 전사용 16kHz mono 파생 WAV만 만들고 작업 후 삭제합니다. 원본의 스테레오·다중 음성 트랙은 그대로 보존합니다.
- 기존 전사(수동 또는 SRT)가 있으면 이를 우선 사용하며 자동 전사로 덮어쓰지 않습니다. 트랙 선택은 재생 및 **첫 자동 전사**에 적용됩니다. 기존 전사를 다른 트랙으로 다시 만들려면 현재는 별도 경기로 가져오세요.
- **실제 모델 추론과 한국어 인식 품질은 이번 환경에서 검증하지 않았습니다.**

## Linux 개발 / 클라우드

```bash
bash scripts/setup-linux.sh
.venv/bin/python -m delta_review doctor
.venv/bin/python -m delta_review --data-dir .delta-review
.venv/bin/python -m pytest -q
```

GUI는 데스크톱 세션이 필요합니다. 디스플레이 없는 자동 테스트는 `QT_QPA_PLATFORM=offscreen`을 사용합니다. 최소 Linux 이미지에서 PipeWire client 설정이 없으면 앱은 Qt의 지원 옵션 `QT_AUDIO_BACKEND=pulseaudio`를 사용합니다. 클라우드 명령 샌드박스가 오디오 소켓을 차단하면 UI 테스트를 승인된 실행 경로에서 실행하세요. FFmpeg와 PyAV 분석 테스트는 기본 샌드박스에서도 검증했습니다. 오디오 출력 장치가 없는 환경에서 실제 청취를 검증했다고 주장하지 않습니다.

CLI도 제공합니다:

```bash
.venv/bin/python -m delta_review --data-dir .delta-review import /path/to/match.mp4
.venv/bin/python -m delta_review --data-dir .delta-review list
.venv/bin/python -m delta_review --data-dir .delta-review analyze <match-id>
```

같은 보관함은 한 인스턴스만 열 수 있습니다. GUI가 열려 있으면 같은 보관함에 대한 CLI를 종료 코드 2로 거부합니다.

## 테스트 자료

실제 경기 영상이 없어 테스트용 색상 전환·스테레오 톤·시각 오프셋 영상으로 처리 흐름을 확인했습니다. 테스트 자료는 게임 장면이나 전사 모델 결과를 흉내 내지 않습니다.

```bash
.venv/bin/python scripts/demo.py
.venv/bin/python -m delta_review --data-dir .delta-review/demo/library
```

데모는 명시적으로 실행할 때만 생성되며 UI에 `[데모]`가 표시됩니다. 데모 자막은 수동 테스트 데이터이고 실제 음성 전사 결과가 아닙니다.

## 저장과 개인정보

- 보관함: `delta-review.sqlite3`, `matches/<id>/original.*`. SQLite에 근거·전사·사건·보고서·설정·피드백 의견을 보관합니다. 기본 저장 한도 50GB, 보관 기간 30일.
- 경기 삭제는 앱 내부 사본과 관련 DB 기록을 지웁니다. 가져오기 전 원본이나 사용자가 내보낸 외부 클립은 지우지 않습니다. 내보내기 전에 아군·본인 음성 포함 가능성을 확인합니다.
- 보관 기간 정리는 삭제 대상 확인 후 실행합니다. 자동 삭제·자동 외부 전송은 없습니다.
- 앱은 HTTP AI 제공자를 구현하지 않으며 API 키를 읽거나 기록하지 않습니다. 외부 AI용 인터페이스만 분리돼 있습니다.
- Git에는 소스·설치 안내·테스트·명시적 합성 데모 스크린샷만 보관합니다. 개인 녹화·음성·자막·DB·로그·환경 변수 파일·키·다운로드한 모델은 `.gitignore`로 제외합니다. 제외된 데이터는 다른 기기로 자동 동기화되지 않습니다.
- 게임 프로세스·메모리·패킷·입력에 접근하지 않습니다. 직접 캡처나 안티치트 우회가 없습니다. OS 캡처의 정책 허용 여부도 아직 확인되지 않았습니다.

구조: [ARCHITECTURE.md](docs/ARCHITECTURE.md) · 검증: [VALIDATION.md](docs/VALIDATION.md) · 기술/정책 출처: [SOURCES.md](docs/SOURCES.md)
