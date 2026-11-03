"""DriverThermalQC entry point.

Usage:
    python main.py                 # normal start (auto camera detection)
    python main.py --simulated     # attach the simulated camera (development)
"""
from __future__ import annotations

import argparse
import sys

from PySide6.QtGui import QColor, QPalette
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


def apply_light_palette(app: QApplication) -> None:
    """Force the light color scheme regardless of the OS dark-mode setting.

    Qt 6 derives its default palette from the Windows color scheme. When the
    OS runs in dark mode every text renders white, while the application
    stylesheet paints light backgrounds - the result is unreadable
    white-on-white text. An explicit palette keeps the UI legible in both
    OS themes (the stylesheet below reinforces it per widget class).
    """
    text = QColor("#1a1f27")
    disabled = QColor("#9aa1ad")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f4f5f7"))
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f7f8fa"))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2a2f3a"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#b3261e"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#2563eb"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, disabled)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    app.setPalette(palette)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    apply_light_palette(app)
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
/* Every rule sets an explicit foreground color so the app can never fall
   back to the OS palette (dark mode -> white text on these light
   backgrounds). apply_light_palette() covers the remaining widgets. */
QMainWindow, QDialog { background: #f4f5f7; color: #1a1f27; }
QLabel { color: #1a1f27; }
QGroupBox { font-weight: 600; border: 1px solid #d7dbe2; border-radius: 6px;
            margin-top: 10px; padding-top: 6px; color: #1a1f27; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px;
                   color: #1a1f27; }
QPushButton { padding: 6px 14px; border: 1px solid #c6cbd4; border-radius: 4px;
              background: #ffffff; color: #1a1f27; }
QPushButton:hover { background: #eef2f8; }
QPushButton:pressed { background: #dfe5ee; }
QPushButton:disabled { color: #9aa1ad; background: #eef0f3; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { padding: 4px 8px;
              border: 1px solid #c6cbd4; border-radius: 4px; background: #ffffff;
              color: #1a1f27; }
QComboBox QAbstractItemView { background: #ffffff; color: #1a1f27;
              selection-background-color: #2563eb; selection-color: #ffffff; }
QCheckBox, QRadioButton { color: #1a1f27; }
QTableWidget { border: 1px solid #d7dbe2; gridline-color: #e4e7ec;
               background: #ffffff; color: #1a1f27; }
QTableWidget::item { color: #1a1f27; }
QHeaderView::section { background: #eef0f4; padding: 4px; border: none;
                       border-right: 1px solid #d7dbe2; font-weight: 600;
                       color: #1a1f27; }
QTextEdit { border: 1px solid #d7dbe2; background: #ffffff; color: #1a1f27; }
QStatusBar { background: #e9ecf1; color: #1a1f27; }
QMenu { background: #ffffff; color: #1a1f27; }
QMenu::item:selected { background: #e8eefc; }
QToolTip { background: #2a2f3a; color: #ffffff; border: 1px solid #1a1f27; }
"""


if __name__ == "__main__":
    raise SystemExit(main())
