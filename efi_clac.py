#!/usr/bin/env python3
import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QDoubleSpinBox, QSpinBox,
    QGridLayout, QHBoxLayout, QLineEdit
)

class EfficiencyCalculator(QWidget):
    AVOGADRO = 6.022e23
    ELEMENTARY_CHARGE = 1.602e-19  # C

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Efficiency Calculator")

        # --- INPUT CONTROLS ---
        # Mass in mg
        self.massSpin = QDoubleSpinBox()
        self.massSpin.setRange(0.0, 1e6)
        self.massSpin.setDecimals(3)
        self.massSpin.setSuffix(" mg")
        self.massSpin.setValue(2.76)

        # Ratio numerator / denominator
        self.ratioNum = QDoubleSpinBox()
        self.ratioNum.setRange(0.0, 1e6)
        self.ratioNum.setDecimals(3)
        self.ratioNum.setValue(1.00)

        self.ratioDen = QDoubleSpinBox()
        self.ratioDen.setRange(1e-6, 1e6)   # avoid zero
        self.ratioDen.setDecimals(3)
        self.ratioDen.setValue(3.00)

        # Molar mass [g/mol]
        self.mmSpin = QDoubleSpinBox()
        self.mmSpin.setRange(0.0, 1e4)
        self.mmSpin.setDecimals(3)
        self.mmSpin.setSuffix(" g/mol")
        self.mmSpin.setValue(101.96)

        # Number of target atoms per molecule
        self.numAtomsSpin = QSpinBox()
        self.numAtomsSpin.setRange(1, 100)
        self.numAtomsSpin.setValue(2)

        # Measured charge [nC]
        self.chargeSpin = QDoubleSpinBox()
        self.chargeSpin.setRange(0.0, 1e9)
        self.chargeSpin.setDecimals(3)
        self.chargeSpin.setSuffix(" nC")
        self.chargeSpin.setValue(1.311e7)

        # --- OUTPUT DISPLAYS ---
        self.availableOut = QLineEdit(); self.availableOut.setReadOnly(True)
        self.producedOut  = QLineEdit(); self.producedOut.setReadOnly(True)
        self.effOut       = QLineEdit(); self.effOut.setReadOnly(True)

        # --- LAYOUT ---
        layout = QGridLayout()
        layout.addWidget(QLabel("Mass in mg:"),                 0, 0)
        layout.addWidget(self.massSpin,                         0, 1)

        layout.addWidget(QLabel("Ratio:"),                      1, 0)
        ratioBox = QHBoxLayout()
        ratioBox.addWidget(self.ratioNum)
        ratioBox.addWidget(QLabel("/"))
        ratioBox.addWidget(self.ratioDen)
        layout.addLayout(ratioBox,                              1, 1)

        layout.addWidget(QLabel("Molar mass [g/mol]:"),         2, 0)
        layout.addWidget(self.mmSpin,                           2, 1)

        layout.addWidget(QLabel("Target atoms/molecule:"),      3, 0)
        layout.addWidget(self.numAtomsSpin,                     3, 1)

        layout.addWidget(QLabel("Measured Charge [nC]:"),       4, 0)
        layout.addWidget(self.chargeSpin,                       4, 1)

        # spacer
        layout.addWidget(QLabel(""), 5, 0, 1, 2)

        layout.addWidget(QLabel("Available Atoms/Molecules:"),  6, 0)
        layout.addWidget(self.availableOut,                     6, 1)

        layout.addWidget(QLabel("Produced Ions:"),              7, 0)
        layout.addWidget(self.producedOut,                      7, 1)

        layout.addWidget(QLabel("Efficiency [%]:"),             8, 0)
        layout.addWidget(self.effOut,                           8, 1)

        self.setLayout(layout)

        # --- SIGNALS ---
        for w in (
            self.massSpin,
            self.ratioNum, self.ratioDen,
            self.mmSpin, self.numAtomsSpin,
            self.chargeSpin
        ):
            w.valueChanged.connect(self.updateCalculations)

        # initial compute
        self.updateCalculations()

    def updateCalculations(self):
        # 1) read inputs
        mass_mg  = self.massSpin.value()
        num      = self.ratioNum.value()
        den      = self.ratioDen.value() or 1e-12
        M        = self.mmSpin.value() or 1e-12
        n_target = self.numAtomsSpin.value()
        Q_nC     = self.chargeSpin.value()

        # 2) convert mg → g
        g = mass_mg / 1000.0

        # 3) corrected mass: g * num / (num + den)
        corrected_mass = (g * num) / (num + den) if (num + den) != 0 else 0.0

        # 4) moles available = corrected_mass / M
        moles_avail = corrected_mass / M

        # 5) available atoms = moles * Avogadro * n_target
        avail_atoms = moles_avail * self.AVOGADRO * n_target

        # 6) produced ions = Q[nC]/(1e9 * e)
        produced = Q_nC / (1e9 * self.ELEMENTARY_CHARGE)

        # 7) efficiency [%] = (produced / available) * 100
        eff = (produced / avail_atoms * 100.0) if avail_atoms != 0 else 0.0

        # 8) display results
        self.availableOut.setText(f"{avail_atoms:.5e}")
        self.producedOut .setText(f"{produced:.5e}")
        self.effOut      .setText(f"{eff:.5f}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = EfficiencyCalculator()
    w.show()
    sys.exit(app.exec_())
