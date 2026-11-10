"""Akustische Aktivierung: einmal Klatschen ODER einmal Fingerschnipsen öffnet Jarvis (holt ihn nach vorn).

Wirkung ist ausschließlich "Jarvis öffnen/fokussieren": kein Auftrag, keine Freigabe, kein Graph-Resume, keine
Identitätsprüfung (ein Klatschen aus dem Lautsprecher oder von einer anderen Person ist nicht unterscheidbar).
Standardmäßig AUS; Einschalten nur über ausdrückliches Setup (python -m jarvis.activation setup).

Module: settings (Einstellungen), capture (Mikrofon -> Ringpuffer), detector (Impuls-Vorfilter + YAMNet +
Entscheidung), launcher (öffnen/fokussieren/starten), service (Ablauf, Zustände), menubar (Status in der Menüleiste).
"""
