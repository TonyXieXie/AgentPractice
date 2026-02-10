; C++ tag definitions for code map extraction.
; These patterns require either a function_definition or an explicit type in a declaration,
; which filters out macro-style calls like LAYOUT_FIELD(...).

(struct_specifier name: (type_identifier) @name.definition.class body: (_)) @definition.class
(declaration type: (union_specifier name: (type_identifier) @name.definition.class)) @definition.class
(class_specifier name: (type_identifier) @name.definition.class) @definition.class

(type_definition declarator: (type_identifier) @name.definition.type) @definition.type
(enum_specifier name: (type_identifier) @name.definition.type) @definition.type

(function_definition
  declarator: (function_declarator declarator: (identifier) @name.definition.function)
) @definition.function
(function_definition
  declarator: (function_declarator declarator: (field_identifier) @name.definition.function)
) @definition.function
(function_definition
  declarator: (function_declarator declarator: (qualified_identifier scope: (_) @local.scope name: (identifier) @name.definition.method))
) @definition.method

(declaration
  type: (_)
  declarator: (function_declarator declarator: (identifier) @name.definition.function)
) @definition.function
(declaration
  type: (_)
  declarator: (function_declarator declarator: (field_identifier) @name.definition.function)
) @definition.function
(declaration
  type: (_)
  declarator: (function_declarator declarator: (qualified_identifier scope: (_) @local.scope name: (identifier) @name.definition.method))
) @definition.method

(field_declaration
  type: (_)
  declarator: (function_declarator declarator: (identifier) @name.definition.function)
) @definition.function
(field_declaration
  type: (_)
  declarator: (function_declarator declarator: (field_identifier) @name.definition.function)
) @definition.function
(field_declaration
  type: (_)
  declarator: (function_declarator declarator: (qualified_identifier scope: (_) @local.scope name: (identifier) @name.definition.method))
) @definition.method
