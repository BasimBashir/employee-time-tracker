"""PyInstaller entry point for the Windows Service.

Built with: python -m PyInstaller --onefile --name service_main --paths . installer/service_main_entry.py

Absolute imports are required here: a frozen script runs as top-level
``__main__``, so the relative imports inside service.service_main cannot resolve.

When the Service Control Manager starts the exe it passes no arguments; in that
case we host the service instead of falling through to the CLI default.
"""
import sys

from service.service_main import TrackerService, main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        import servicemanager
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(TrackerService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        main()
