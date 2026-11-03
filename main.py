"""DriverThermalQC entry point.

Usage:
    python main.py                 # normal start (auto camera detection)
    python main.py --simulated     # attach the simulated camera (development)
"""
from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.application import ApplicationServices
from app.core.constants import APP_NAME, APP_TITLE, APP_VERSION, ORG_NAME
from app.logging.log_manager import get_logger

logger = get_logger("main")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=APP_TITLE)
    parser.add_argument("--simulated", action="store_true",
                        help="attach the simulated camera (synthetic data)")
    parser.add_argument("--data-dir", default=None,
                        help="override the application data directory")
    return parser.parse_args(argv)


def ensure_database_health(services: ApplicationServices, parent=None) -> bool:
    """Startup integrity check with an explicit, logged restore flow."""
    ok, message = services.db.integrity_check()
    if ok:
        return True
    logger.error("Database integrity check failed: %s", message)
    from app.ui.dialogs.confirmations import DatabaseRestoreDialog

    dialog = DatabaseRestoreDialog(
        message, services.backup.backup_is_valid(), parent
    )
    dialog.exec()
    if dialog.wants_restore:
        try:
            report = services.backup.restore_backup()
            logger.info("Database restored: %s", report)
        except Exception as exc:
            logger.error("Restore failed: %s", exc)
            QMessageBox.critical(
                parent, "Restore Failed",
                f"The database could not be restored:\n{exc}\n\n"
                "The application will now exit.",
            )
            return False
        # Re-open a fresh connection against the restored file.
        services.db = type(services.db)(services.db_path)
        services.reports = type(services.reports)(services.db)
        QMessageBox.information(
            parent, "Database Restored",
            "The database was restored from the verified backup and validated.",
        )
        return True
    if dialog.wants_continue:
        logger.warning("User chose to continue with a possibly corrupt database")
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(_APP_STYLESHEET)

    services = ApplicationServices(data_dir=args.data_dir,
                                   attach_simulated=args.simulated)

    from app.ui.main_window import MainWindow

    window = MainWindow(services)
    if not ensure_database_health(services, window):
        services.shutdown(normal=False)
        return 1
    window.show()
    logger.info("%s v%s started", APP_TITLE, APP_VERSION)
    try:
        return app.exec()
    finally:
        # shutdown() is a no-op when the window already handled it.
        services.shutdown(normal=True)


_APP_STYLESHEET = """
QMainWindow, QDialog { background: #f4f5f7; }
QGroupBox { font-weight: 600; border: 1px solid #d7dbe2; border-radius: 6px;
            margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton { padding: 6px 14px; border: 1px solid #c6cbd4; border-radius: 4px;
              background: #ffffff; }
QPushButton:hover { background: #eef2f8; }
QPushButton:pressed { background: #dfe5ee; }
QPushButton:disabled { color: #9aa1ad; background: #eef0f3; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { padding: 4px 8px;
              border: 1px solid #c6cbd4; border-radius: 4px; background: #ffffff; }
QTableWidget { border: 1px solid #d7dbe2; gridline-color: #e4e7ec;
               background: #ffffff; }
QHeaderView::section { background: #eef0f4; padding: 5px; border: none;
                       border-right: 1px solid #d7dbe2; font-weight: 600; }
QTextEdit { border: 1px solid #d7dbe2; background: #ffffff; }
QStatusBar { background: #e9ecf1; }
"""


if __name__ == "__main__":
    raise SystemExit(main())
