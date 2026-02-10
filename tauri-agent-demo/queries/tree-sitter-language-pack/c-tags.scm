; C tag definitions for code map extraction.

(struct_specifier name: (type_identifier) @name.definition.class body: (_)) @definition.class
(declaration type: (union_specifier name: (type_identifier) @name.definition.class)) @definition.class

(type_definition declarator: (type_identifier) @name.definition.type) @definition.type
(enum_specifier name: (type_identifier) @name.definition.type) @definition.type

(function_definition
  declarator: (function_declarator declarator: (identifier) @name.definition.function)
) @definition.function

(declaration
  type: (_)
  declarator: (function_declarator declarator: (identifier) @name.definition.function)
) @definition.function

(field_declaration
  type: (_)
  declarator: (function_declarator declarator: (identifier) @name.definition.function)
) @definition.function
