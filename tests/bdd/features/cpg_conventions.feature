# language: es
# tests/features/cpg_conventions.feature
#
# Feature: Chequeo de convenciones del CKG contra el código real.
#
# Las convenciones son KnowledgeFacts type="convention" en el CKG, con la
# regla ast-grep YAML en metadata.rule_yaml. CheckConventionsUseCase las
# carga del CKG y las ejecuta contra el repo con ast-grep.

Característica: Chequeo de convenciones del CKG contra el código
  Como arquitecto del módulo CPG
  Quiero validar las convenciones registradas en el CKG contra el código real
  Para detectar violaciones (ej. except:pass) sin scans manuales

  Antecedentes:
    Dado un proyecto registrado con repositorio resoluble y CKG con convenciones

  @contrato
  Escenario: Las convenciones del CKG se ejecutan contra el código
    Dado un chain_dir del proyecto walker registrado
    Cuando se ejecuta el chequeo de convenciones
    Entonces el reporte indica al menos una regla comprobada
    Y cada violación reportada tiene estructura de convención (rule, severity, file, line, text)
    Y las violaciones se agrupan por rule_id en el reporte
    Y no se reportan errores de ejecución

  @fallback
  Escenario: Un repositorio irresoluble no produce violaciones
    Dado un chain_dir inexistente
    Cuando se ejecuta el chequeo de convenciones
    Entonces no hay violaciones reportadas
    Y rules_checked es cero
