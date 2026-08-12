; ============================================
; 金价监控 Windows 安装包脚本 (Inno Setup)
;
; 前置：
;   1. 先运行 build_win.bat 生成 dist\GoldPrice.exe
;   2. 安装 Inno Setup 6+ (https://jrsoftware.org/isdl.php)
;   3. 用 Inno Setup 打开本文件 -> Build -> Compile
;
; 产物：installer\GoldPrice-Setup.exe
; ============================================

#define MyAppName "金价监控"
#define MyAppVersion "1.0.0"
#define MyAppExeName "GoldPrice.exe"

[Setup]
AppId={{8F2A6C41-3E5B-4B9A-9D0C-7A1B2C3D4E5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\GoldPrice
DefaultGroupName={#MyAppName}
OutputDir=installer
OutputBaseFilename=GoldPrice-Setup
SetupIconFile=build\icon.ico
Compression=lzma2
SolidCompression=yes
; 卸载时保留用户数据（数据在 %USERPROFILE%\.goldprice，不在安装目录）
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载{#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动{#MyAppName}"; Flags: nowait postinstall skipifsilent
