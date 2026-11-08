# 기술·라이선스·게임 정책 확인

확인일: **2026-11-08 (한국 시간)**. 공식 소스 저장소와 PyPI 게시 메타데이터, 설치된 배포물의 메타데이터를 확인했습니다. 링크의 내용을 읽지 못한 경우 읽은 것처럼 요약하지 않았습니다.

| 기술 | 선택과 확인 결과 | 출처 |
|---|---|---|
| Python | 환경 실측 3.12.14. Windows 목표 3.12 x64. | `python --version`, https://www.python.org/downloads/windows/ (사용자 설치 안내 링크) |
| PySide6 6.12.0 | Qt 공식 Python 바인딩. Python >=3.10, 데스크톱 UI·멀티미디어 사용. LGPL-3.0-only 또는 GPL-2.0/3.0 메타데이터 확인. Windows x64 휠 제공 확인. | https://pypi.org/pypi/PySide6/json (조회 성공), https://github.com/qt/qtmultimedia/blob/dev/src/multimedia/platform/qplatformaudiodevices.cpp (공식 코드 조회 성공) |
| FFmpeg 7.1.5 (시스템) | 원본 probe, WAV·클립. 기본 LGPL2.1+, 선택 구성에 따라 GPL. 이 Linux 빌드는 `--enable-gpl`/libx264 포함. 앱에 실행파일을 재배포하지 않음. | https://github.com/FFmpeg/FFmpeg/blob/master/LICENSE.md (조회 성공), `ffmpeg -version` 실측 |
| PyAV 16.1.0 | 실제 PTS와 VFR 프레임 디코딩, BSD-3-Clause 메타데이터. 번들 FFmpeg의 별도 라이선스도 확인 대상. | https://github.com/PyAV-Org/PyAV, 설치 패키지 메타데이터 |
| OpenCV headless 4.14.0.94 | 프레임 차분만 사용, 게임 전용 인식 모델로 간주하지 않음. Apache 2.0, Windows 휠 확인. headless 패키지로 Qt 플러그인 충돌 회피. | https://github.com/opencv/opencv/blob/4.x/LICENSE (조회 성공), 설치 패키지 메타데이터 |
| faster-whisper 1.2.1 | MIT. README의 CPU int8/Windows 의존성/로컬 모델 로딩을 확인. 기본 CPU 실행으로 CUDA 요구를 피함. 모델은 별도이며 해당 가중치 라이선스도 확인 필요. | https://github.com/SYSTRAN/faster-whisper (README 조회 성공) |
| SQLite | Python 표준 sqlite3, 로컬 WAL DB. 별도 서버 불필요. | Python 런타임의 실제 CRUD/트랜잭션 테스트 |
| NumPy / psutil | 프레임 통계와 실행 환경 점검. 배포 메타데이터 라이선스는 requirements 설치물 참조(BSD 계열 등). | 설치 패키지 메타데이터 |

PyPI에서 현재 제공하는 버전을 설치하고 고정했습니다. 설치 성공이 장기 유지보수 보장이나 Windows 실사용 검증을 의미하지 않습니다. Qt 웹엔진/PDF는 PySide6 배포의 전이 의존성으로 설치됐지만 앱에서 사용하지 않습니다. 공식 Qt 웹 문서 `doc.qt.io`와 FFmpeg 웹 라이선스 페이지 `ffmpeg.org/legal.html`은 현재 클라우드 접근에서 HTTP 403을 받았습니다. 공식 GitHub 소스에서 확인 가능한 내용을 사용했습니다.

Windows 타깃 `pip --dry-run --ignore-installed --only-binary=:all: --platform win_amd64 --python-version 312 --implementation cp --abi cp312 -r requirements-lock.txt`의 해결·휠 조회는 성공했습니다. Windows 전용 조건부 의존성은 실제 Windows 설치 시 다시 평가되므로 이를 완전한 Windows 설치 검증으로 보지 않습니다.

## 델타포스 정책·안티치트

공식 글로벌 사이트 진입점 https://www.playdeltaforce.com/ 에 2026-11-08 접근을 시도했으나 현재 클라우드 네트워크에서 HTTP 403을 받아 약관·안티치트 안내 본문을 확인하지 못했습니다. 사용자 지역(글로벌/가레나 등)과 실제 이용 서비스가 제공되지 않아 적용되는 운영사/약관 버전도 확정하지 않았습니다.

따라서 **이 프로그램이나 OS 캡처가 운영사 정책상 허용된다고 확인하지 않았습니다.** 이번 단계는 사용자가 이미 확보한 파일만 분석하며 게임 실행 중 프로세스 접근, 자동 입력, 메모리 읽기, DLL 주입, 패킷 가로채기, 차단 우회를 구현하지 않습니다. 직접 수집을 도입하기 전에 실제 지역의 공식 약관·보안 안내 URL, 게시/개정일, 확인일, 녹화·외부 프로그램 관련 문구와 해석상 불확실성을 기록해야 합니다. 차단 시 우회 없이 파일 가져오기를 유지합니다.

지도·무기·오퍼레이터 데이터셋이나 웹 자료를 가져오지 않았으므로 검증되지 않은 게임 수치·위치도 사용하지 않습니다.
