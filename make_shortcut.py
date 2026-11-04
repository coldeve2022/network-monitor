"""Create portable Windows shortcuts; --dry-run makes no changes."""
import argparse
import os
from pathlib import Path
import subprocess


def make_shortcut(destination, target):
    destination, target = Path(destination).resolve(), Path(target).resolve()
    if not target.is_file():
        raise FileNotFoundError(f'请先构建程序：{target}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, NM_SHORTCUT=str(destination), NM_TARGET=str(target))
    script = '''$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($env:NM_SHORTCUT)
$shortcut.TargetPath = $env:NM_TARGET
$shortcut.WorkingDirectory = [System.IO.Path]::GetDirectoryName($env:NM_TARGET)
$shortcut.IconLocation = $env:NM_TARGET + ',0'
$shortcut.Description = '网络质量监测'
$shortcut.Save()
'''
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   env=environment, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return destination


def main():
    parser = argparse.ArgumentParser(description='创建网络监测快捷方式')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    target = Path(__file__).resolve().parent / 'dist' / 'NetworkMonitor.exe'
    desktop = subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',"[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();[Environment]::GetFolderPath('Desktop')"],text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip()
    destinations = [Path(desktop) / '网络监测 NetworkMonitor.lnk',
                    Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/网络监测 NetworkMonitor.lnk']
    for destination in destinations:
        if args.dry_run:
            print(f'{destination} -> {target}')
        else:
            print(make_shortcut(destination, target))


if __name__ == '__main__':
    main()
