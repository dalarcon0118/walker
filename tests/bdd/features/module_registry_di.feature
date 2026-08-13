Feature: Module registry DI
  Como arquitecto de inyección de dependencias
  Quiero que el ModuleRegistry registre servicios por clave lógica con resolución lazy
  Y que los features reciban servicios sin importar implementaciones concretas

  Scenario: Registra y resuelve servicios por clave
    Given un ModuleRegistry vacío
    When registro el servicio "path_resolver"
    Then el servicio existe en el registry
    And al resolverlo obtengo una instancia
    And aparece en list_services

  Scenario: La factory lazy es singleton cacheado
    Given un ModuleRegistry con factory lazy para "searcher"
    When resuelvo "searcher" dos veces
    Then obtengo la misma instancia
    And la factory se construyó solo una vez

  Scenario: Servicio no registrado lanza KeyError
    Given un ModuleRegistry vacío
    When intento resolver "no_existe"
    Then se lanza KeyError

  Scenario: Bootstrap registra servicios de shared/
    Given el composition root bootstrap
    When inicializo el registry con bootstrap_modules
    Then "path_resolver" está registrado y es callable
    And "cognitive_searcher" está registrado como factory callable
    And "mcp_server" está registrado como lazy singleton

  Scenario: El feature recibe servicios por IoC sin importar shared
    Given un registry con "cognitive_searcher" lazy
    When construyo un adapter resolviendo por clave
    Then obtengo el servicio sin importar implementaciones
