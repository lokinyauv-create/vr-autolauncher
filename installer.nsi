; VR Auto Launcher installer (per-user, no admin required)

!define APP_NAME "VR Auto Launcher"
!define APP_VERSION "1.0.0"
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
    File "dist_build\dist\${APP_EXE}"

    ; Drop the old raw-script Startup entries if present, so we never end
    ; up with two copies running (this bit us earlier this session).
    Delete "$SMSTARTUP\vr_autolauncher_tray.pyw"
    Delete "$SMSTARTUP\vr_autolauncher_tray.py"

    CreateShortCut "$SMSTARTUP\VR Auto Launcher.lnk" "$INSTDIR\${APP_EXE}"

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
    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
    DeleteRegKey HKCU "${UNINSTALL_REG_KEY}"
SectionEnd
