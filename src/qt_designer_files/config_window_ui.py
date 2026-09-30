# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'config_window.ui'
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
from PySide6.QtWidgets import (QApplication, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSizePolicy, QWidget)

class Ui_Form(object):
    def setupUi(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        Form.resize(488, 398)
        Form.setStyleSheet(u"background-color: rgb(18, 18, 17);\n"
"color: rgb(238, 118, 60);")
        self.bt_save_config = QPushButton(Form)
        self.bt_save_config.setObjectName(u"bt_save_config")
        self.bt_save_config.setGeometry(QRect(40, 330, 161, 41))
        self.bt_save_config.setToolTipDuration(5000)
        self.bt_save_config.setStyleSheet(u"background-color: rgb(238, 118, 60);\n"
"color: rgb(0, 0, 0);\n"
"font: 900 10pt \"Segoe UI\";\n"
"border-radius: 10px;\n"
"border: 2px solid rgb(238, 118, 60);")
        self.bt_load_config = QPushButton(Form)
        self.bt_load_config.setObjectName(u"bt_load_config")
        self.bt_load_config.setGeometry(QRect(270, 330, 181, 41))
        self.bt_load_config.setToolTipDuration(5000)
        self.bt_load_config.setStyleSheet(u"background-color: rgb(238, 118, 60);\n"
"color: rgb(0, 0, 0);\n"
"font: 900 10pt \"Segoe UI\";\n"
"border-radius: 10px;\n"
"border: 2px solid rgb(238, 118, 60);")
        self.bt_choose_thermoviewer_exe = QPushButton(Form)
        self.bt_choose_thermoviewer_exe.setObjectName(u"bt_choose_thermoviewer_exe")
        self.bt_choose_thermoviewer_exe.setGeometry(QRect(10, 12, 161, 41))
        self.bt_choose_thermoviewer_exe.setToolTipDuration(5000)
        self.bt_choose_thermoviewer_exe.setStyleSheet(u"background-color: rgb(28, 28, 29);\n"
"border-radius: 10px;\n"
"border: 2px solid rgb(238, 118, 60);\n"
"color: rgb(255, 255, 255);")
        self.le_choose_thermoviewer_exe = QLineEdit(Form)
        self.le_choose_thermoviewer_exe.setObjectName(u"le_choose_thermoviewer_exe")
        self.le_choose_thermoviewer_exe.setGeometry(QRect(190, 21, 291, 24))
        self.le_choose_thermoviewer_exe.setToolTipDuration(5000)
        self.le_choose_thermoviewer_exe.setStyleSheet(u"border: 1px solid rgb(238, 118, 60);\n"
"color: rgb(255, 255, 255);")
        self.list_rgb_cropping_percentage_by_models = QListWidget(Form)
        self.list_rgb_cropping_percentage_by_models.setObjectName(u"list_rgb_cropping_percentage_by_models")
        self.list_rgb_cropping_percentage_by_models.setGeometry(QRect(10, 100, 251, 121))
        self.list_rgb_cropping_percentage_by_models.setStyleSheet(u"border: 1px solid rgb(238, 118, 60);\n"
"color: rgb(255, 255, 255);")
        self.lb_list_cropping_percentage = QLabel(Form)
        self.lb_list_cropping_percentage.setObjectName(u"lb_list_cropping_percentage")
        self.lb_list_cropping_percentage.setGeometry(QRect(10, 70, 201, 16))
        self.le_model = QLineEdit(Form)
        self.le_model.setObjectName(u"le_model")
        self.le_model.setGeometry(QRect(280, 120, 101, 24))
        self.le_model.setStyleSheet(u"border: 1px solid rgb(238, 118, 60);\n"
"color: rgb(255, 255, 255);")
        self.lb_model = QLabel(Form)
        self.lb_model.setObjectName(u"lb_model")
        self.lb_model.setGeometry(QRect(280, 100, 61, 16))
        self.lb_percentage = QLabel(Form)
        self.lb_percentage.setObjectName(u"lb_percentage")
        self.lb_percentage.setGeometry(QRect(400, 100, 61, 16))
        self.le_percentage = QLineEdit(Form)
        self.le_percentage.setObjectName(u"le_percentage")
        self.le_percentage.setGeometry(QRect(400, 120, 61, 24))
        self.le_percentage.setStyleSheet(u"border: 1px solid rgb(238, 118, 60);\n"
"color: rgb(255, 255, 255);")
        self.bt_modify_add_cropping_values = QPushButton(Form)
        self.bt_modify_add_cropping_values.setObjectName(u"bt_modify_add_cropping_values")
        self.bt_modify_add_cropping_values.setGeometry(QRect(280, 160, 181, 31))
        self.bt_modify_add_cropping_values.setToolTipDuration(5000)
        self.bt_modify_add_cropping_values.setStyleSheet(u"background-color: rgb(28, 28, 29);\n"
"border-radius: 10px;\n"
"border: 2px solid rgb(238, 118, 60);\n"
"color: rgb(255, 255, 255);")

        self.retranslateUi(Form)

        QMetaObject.connectSlotsByName(Form)
    # setupUi

    def retranslateUi(self, Form):
        Form.setWindowTitle(QCoreApplication.translate("Form", u"Editar configuraci\u00f3n", None))
#if QT_CONFIG(tooltip)
        self.bt_save_config.setToolTip(QCoreApplication.translate("Form", u"Guarda la configuraci\u00f3n en un archivo a elegir.", None))
#endif // QT_CONFIG(tooltip)
        self.bt_save_config.setText(QCoreApplication.translate("Form", u"Guardar configuraci\u00f3n", None))
#if QT_CONFIG(tooltip)
        self.bt_load_config.setToolTip(QCoreApplication.translate("Form", u"Carga la configuraci\u00f3n para mostrar en la ventana a partir de un archivo.", None))
#endif // QT_CONFIG(tooltip)
        self.bt_load_config.setText(QCoreApplication.translate("Form", u"Cargar configuraci\u00f3n", None))
#if QT_CONFIG(tooltip)
        self.bt_choose_thermoviewer_exe.setToolTip(QCoreApplication.translate("Form", u"Elige la ubicaci\u00f3n del ejecutable del Thermoviewer.", None))
#endif // QT_CONFIG(tooltip)
        self.bt_choose_thermoviewer_exe.setText(QCoreApplication.translate("Form", u"Elige ubicaci\u00f3n ejecutable\n"
"Thermoviewer", None))
#if QT_CONFIG(tooltip)
        self.le_choose_thermoviewer_exe.setToolTip(QCoreApplication.translate("Form", u"Muestra la ubicaci\u00f3n del ejecutable del Thermoviewer.", None))
#endif // QT_CONFIG(tooltip)
        self.lb_list_cropping_percentage.setText(QCoreApplication.translate("Form", u"Porcentaje de recorte por dispositivo", None))
        self.lb_model.setText(QCoreApplication.translate("Form", u"Modelo", None))
        self.lb_percentage.setText(QCoreApplication.translate("Form", u"Porcentaje", None))
#if QT_CONFIG(tooltip)
        self.bt_modify_add_cropping_values.setToolTip(QCoreApplication.translate("Form", u"Elige la ubicaci\u00f3n del ejecutable del Thermoviewer.", None))
#endif // QT_CONFIG(tooltip)
        self.bt_modify_add_cropping_values.setText(QCoreApplication.translate("Form", u"Modificar/A\u00f1adir valores", None))
    # retranslateUi

