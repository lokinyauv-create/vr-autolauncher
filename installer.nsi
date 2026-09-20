; VR Auto Launcher installer (per-user, no admin required).
;
; Build the exe first (PyInstaller bundles Python and PySide6, so the machine
; that installs it needs nothing else):
;
;   pip install pyinstaller pyside6
;   pyinstaller --onefile --windowed --name VRAutoLauncher --icon vr-autolauncher.ico ^
;               vr_autolauncher_tray.pyw
;   makensis installer.nsi
;
; For a plain copy of the sources instead, use windows\install.ps1.

!define APP_NAME "VR Auto Launcher"
!define APP_VERSION "2.0.0"
!define APP_EXE "VRAutoLauncher.exe"
!define INSTALL_DIR "$LOCALAPPDATA\VRAutoLauncher"
!define UNINSTALL_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRAutoLauncher"

Name "${APP_NAME}"
OutFile "VRAutoLauncher-Setup.exe"
InstallDir "${INSTALL_DIR}"
RequestExecutionLevel user
SetCompressor /SOLID lzma

Page directory
Page instfiles

UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
    SetOutPath "$INSTDIR"
    File "dist\${APP_EXE}"

    ; Drop entries left by older versions, so we never end up with two copies running.
    Delete "$SMSTARTUP\vr_autolauncher_tray.pyw"
    Delete "$SMSTARTUP\vr_autolauncher_tray.py"
    Delete "$SMSTARTUP\VR Auto Launcher.cmd"

    ; --background starts in the tray without opening the settings window.
    CreateShortCut "$SMSTARTUP\VR Auto Launcher.lnk" "$INSTDIR\${APP_EXE}" "--background"
    CreateShortCut "$SMPROGRAMS\VR Auto Launcher.lnk" "$INSTDIR\${APP_EXE}"

    WriteUninstaller "$INSTDIR\Uninstall.exe"

    WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegDWORD HKCU "${UNINSTALL_REG_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTALL_REG_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
    Delete "$SMSTARTUP\VR Auto Launcher.lnk"
    Delete "$SMPROGRAMS\VR Auto Launcher.lnk"
    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\Uninstall.exe"
    ; config.json and the log stay behind on purpose.
    RMDir "$INSTDIR"
    DeleteRegKey HKCU "${UNINSTALL_REG_KEY}"
SectionEnd
