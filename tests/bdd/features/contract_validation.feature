# language: es
# tests/features/contract_validation.feature
#
# Feature: Contract validation (anti-alucinacion determinista)
#
# Como motor del workflow
# Quiero que verify_post valide el output contra output_schema
# Para bloquear datos que no cumplen el contrato y evitar alucinaciones

Característica: Validacion por contratos anti-alucinacion determinista
  Como motor del workflow
  Quiero que verify_post valide el output contra output_schema
  Para bloquear datos que no cumplen el contrato y evitar alucinaciones

  Antecedentes:
    Dado que existe un contrato de salida con target y reason

  @contract
  Escenario: verify_post bloquea required faltante
    Cuando se valida un output vacio contra el contrato
    Entonces la validacion no pasa
    Y los errores mencionan target
    Y los errores mencionan reason

  @contract
  Escenario: verify_post bloquea tipo incorrecto
    Cuando se valida un output con target int
    Entonces la validacion no pasa
    Y los errores indican que debe ser string

  @contract
  Escenario: verify_post bloquea valor fuera de enum
    Cuando se valida un output con target nonsense
    Entonces la validacion no pasa
    Y los errores mencionan enum

  @contract
  Escenario: verify_post pasa con output valido
    Cuando se valida un output valido con target implement y reason de 10+ chars
    Entonces la validacion pasa
    Y no hay falsos positivos

  @contract
  Escenario: Contract.from_dict preserva properties
    Cuando se crea un contrato desde el schema
    Entonces required_fields es (target, reason)
    Y properties incluye target
    Y target.type es string
    Y analyze esta en target.enum

  @contract
  Escenario: verify_pre sigue bloqueando por required_fields
    Cuando se valida pre-ejecucion con target vacio
    Entonces la validacion no pasa
    Y los errores mencionan reason

  @contract
  Escenario: SubmitDataUseCase lanza ValidationError con output invalido
    Cuando verify_post falla y se lanza ValidationError
    Entonces la excepcion contiene los errores del contrato
    Y los mensajes mencionan target
    Y los mensajes mencionan reason
