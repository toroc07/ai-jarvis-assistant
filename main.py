"""
Jarvis en modo consola.

Esta es la fase 1: el cerebro, la memoria y el sistema de permisos funcionando
en un chat de texto. La ventana de Qt y la voz se montan encima de esto sin
tocar nada de lo que hay debajo.

    python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permite ejecutar el archivo directamente sin instalar el proyecto.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.agent import Agente  # noqa: E402
from security.guard import Peticion  # noqa: E402

VERDE = "\033[92m"
AMARILLO = "\033[93m"
ROJO = "\033[91m"
GRIS = "\033[90m"
FIN = "\033[0m"


def confirmar_en_consola(peticion: Peticion) -> bool:
    """Muestra la acción y espera tu aprobación.

    Se enseña exactamente qué se va a hacer y sobre qué, porque una
    confirmación que no dice lo que aprueba no protege de nada.
    """
    print(f"\n{AMARILLO}┌─ Jarvis pide permiso ─────────────────────────{FIN}")
    print(f"{AMARILLO}│{FIN} Acción:   {peticion.accion}")
    if peticion.objetivo:
        print(f"{AMARILLO}│{FIN} Sobre:    {peticion.objetivo}")
    if peticion.motivo:
        print(f"{AMARILLO}│{FIN} Para:     {peticion.motivo}")
    print(f"{AMARILLO}└───────────────────────────────────────────────{FIN}")

    try:
        respuesta = input("¿Lo autorizas? (s/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    # Solo un sí explícito autoriza. Cualquier otra cosa, incluido enter, es no.
    return respuesta in ("s", "si", "sí", "y", "yes")


def main() -> int:
    print(f"{VERDE}Jarvis{FIN} - fase 1, consola\n")

    agente = Agente()
    estado = agente.cerebro.estado()

    if estado["local_disponible"]:
        print(f"  {VERDE}●{FIN} Modelo local: {estado['modelo_local']}")
    else:
        print(
            f"  {ROJO}●{FIN} Modelo local no disponible "
            f"({estado['modelo_local']}). ¿Está Ollama arrancado?"
        )
    if estado["claude_disponible"]:
        print(f"  {VERDE}●{FIN} Claude: {estado['modelo_claude']} (para tareas complejas)")
    else:
        print(f"  {GRIS}○{FIN} Claude no configurado (falta ANTHROPIC_API_KEY)")

    if not estado["local_disponible"] and not estado["claude_disponible"]:
        print(f"\n{ROJO}No hay ningún modelo disponible. Jarvis no puede arrancar.{FIN}")
        return 1

    print(f"\n{GRIS}Escribe 'salir' para terminar.{FIN}\n")

    while True:
        try:
            peticion = input(f"{VERDE}tú ›{FIN} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHasta luego.")
            return 0

        if not peticion:
            continue
        if peticion.lower() in ("salir", "adios", "adiós", "exit", "quit"):
            print("Hasta luego.")
            return 0

        try:
            resultado = agente.responder(peticion, confirmar_en_consola)
        except Exception as e:
            print(f"{ROJO}Error: {e}{FIN}\n")
            continue

        etiqueta = "claude" if resultado.motor.value == "claude" else "local"
        print(f"\n{VERDE}jarvis ›{FIN} {resultado.texto}")
        detalle = f"[{etiqueta}]"
        if resultado.acciones:
            detalle += f" acciones: {', '.join(resultado.acciones)}"
        print(f"{GRIS}{detalle}{FIN}\n")


if __name__ == "__main__":
    raise SystemExit(main())
