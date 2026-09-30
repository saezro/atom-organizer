# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'log_window.ui'
##
## Created by: Qt User Interface Compiler version 6.6.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QCheckBox, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget)

class Ui_Form(object):
    def setupUi(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        Form.resize(788, 716)
        Form.setStyleSheet(u"background-color: rgb(18, 18, 17);\n"
"color: rgb(238, 118, 60);")
        self.verticalLayout = QVBoxLayout(Form)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.lb_summarize_process = QLabel(Form)
        self.lb_summarize_process.setObjectName(u"lb_summarize_process")

        self.verticalLayout.addWidget(self.lb_summarize_process)

        self.te_summarize_process = QPlainTextEdit(Form)
        self.te_summarize_process.setObjectName(u"te_summarize_process")
        self.te_summarize_process.setEnabled(True)
        self.te_summarize_process.setMaximumSize(QSize(16777215, 200))
        self.te_summarize_process.setAcceptDrops(False)
        self.te_summarize_process.setToolTipDuration(5000)
        self.te_summarize_process.setStyleSheet(u"background-color: rgb(255, 255, 255);\n"
"color: rgb(0, 0, 0);")
        self.te_summarize_process.setInputMethodHints(Qt.ImhMultiLine|Qt.ImhNoTextHandles)
        self.te_summarize_process.setUndoRedoEnabled(False)
        self.te_summarize_process.setReadOnly(True)
        self.te_summarize_process.setTextInteractionFlags(Qt.NoTextInteraction)

        self.verticalLayout.addWidget(self.te_summarize_process)

        self.lb_log = QLabel(Form)
        self.lb_log.setObjectName(u"lb_log")

        self.verticalLayout.addWidget(self.lb_log)

        self.te_log = QPlainTextEdit(Form)
        self.te_log.setObjectName(u"te_log")
        self.te_log.setToolTipDuration(5000)
        self.te_log.setStyleSheet(u"background-color: rgb(255, 255, 255);\n"
"color: rgb(0, 0, 0);")
        self.te_log.setReadOnly(True)

        self.verticalLayout.addWidget(self.te_log)

        self.bt_clean_logs = QPushButton(Form)
        self.bt_clean_logs.setObjectName(u"bt_clean_logs")
        self.bt_clean_logs.setToolTipDuration(5000)
        self.bt_clean_logs.setStyleSheet(u"background-color: rgb(238, 118, 60);\n"
"color: rgb(0, 0, 0);\n"
"font: 900 10pt \"Segoe UI\";\n"
"border-radius: 10px;\n"
"border: 2px solid rgb(238, 118, 60);")

        self.verticalLayout.addWidget(self.bt_clean_logs)

        self.pb_process = QProgressBar(Form)
        self.pb_process.setObjectName(u"pb_process")
        self.pb_process.setToolTipDuration(5000)
        self.pb_process.setValue(0)

        self.verticalLayout.addWidget(self.pb_process)

        self.cb_enable_stop_process = QCheckBox(Form)
        self.cb_enable_stop_process.setObjectName(u"cb_enable_stop_process")
        self.cb_enable_stop_process.setToolTipDuration(5000)
        self.cb_enable_stop_process.setStyleSheet(u"color: rgb(238, 118, 60);")

        self.verticalLayout.addWidget(self.cb_enable_stop_process)

        self.bt_stop_process = QPushButton(Form)
        self.bt_stop_process.setObjectName(u"bt_stop_process")
        self.bt_stop_process.setEnabled(False)
        self.bt_stop_process.setToolTipDuration(5000)
        self.bt_stop_process.setStyleSheet(u"background-color: rgb(28, 28, 29);\n"
"border-radius: 9px;\n"
"border: 2px solid rgb(144, 144, 144);\n"
"color: rgb(144,144,144);")

        self.verticalLayout.addWidget(self.bt_stop_process)


        self.retranslateUi(Form)

        QMetaObject.connectSlotsByName(Form)
    # setupUi

    def retranslateUi(self, Form):
        Form.setWindowTitle(QCoreApplication.translate("Form", u"Form", None))
        self.lb_summarize_process.setText(QCoreApplication.translate("Form", u"RESUMEN PROCESO", None))
#if QT_CONFIG(tooltip)
        self.te_summarize_process.setToolTip(QCoreApplication.translate("Form", u"Muestra un resumen del proceso, tanto al empezar como al finalizar.", None))
#endif // QT_CONFIG(tooltip)
        self.lb_log.setText(QCoreApplication.translate("Form", u"LOG", None))
#if QT_CONFIG(tooltip)
        self.te_log.setToolTip(QCoreApplication.translate("Form", u"Muestra informaci\u00f3n que ocurre durante el proceso.", None))
#endif // QT_CONFIG(tooltip)
        self.te_log.setPlainText("")
#if QT_CONFIG(tooltip)
        self.bt_clean_logs.setToolTip(QCoreApplication.translate("Form", u"Borra los logs.", None))
#endif // QT_CONFIG(tooltip)
        self.bt_clean_logs.setText(QCoreApplication.translate("Form", u"Limpiar logs", None))
#if QT_CONFIG(tooltip)
        self.pb_process.setToolTip(QCoreApplication.translate("Form", u"Indica el porcentaje de procesado.", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.cb_enable_stop_process.setToolTip(QCoreApplication.translate("Form", u"Habilita la posibilidad de parar el proceso.", None))
#endif // QT_CONFIG(tooltip)
        self.cb_enable_stop_process.setText(QCoreApplication.translate("Form", u"Habilitar \"Parar Proceso\"", None))
#if QT_CONFIG(tooltip)
        self.bt_stop_process.setToolTip(QCoreApplication.translate("Form", u"Para el proceso.", None))
#endif // QT_CONFIG(tooltip)
        self.bt_stop_process.setText(QCoreApplication.translate("Form", u"Parar proceso", None))
    # retranslateUi

