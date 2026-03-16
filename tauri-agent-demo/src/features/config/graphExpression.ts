type TokenKind = 'ident' | 'number' | 'string' | 'op';

type Token = {
    kind: TokenKind;
    value: string;
};

const TOKEN_PATTERN =
    /\s*(?:(?<number>-?\d+(?:\.\d+)?)|(?<string>"(?:\\.|[^"])*"|'(?:\\.|[^'])*')|(?<op>==|!=|>=|<=|>|<|\(|\)|\.)|(?<ident>[A-Za-z_][A-Za-z0-9_]*)|(?<mismatch>.))/y;

class ExpressionSyntaxError extends Error {}

class Parser {
    private readonly tokens: Token[];
    private index = 0;

    constructor(tokens: Token[]) {
        this.tokens = tokens;
    }

    parse(): void {
        this.parseOr();
        if (this.peek()) {
            throw new ExpressionSyntaxError(`Unexpected token '${this.peek()!.value}'`);
        }
    }

    private peek(): Token | undefined {
        return this.tokens[this.index];
    }

    private consume(): Token {
        const token = this.peek();
        if (!token) {
            throw new ExpressionSyntaxError('Unexpected end of expression');
        }
        this.index += 1;
        return token;
    }

    private matchIdent(value: string): boolean {
        const token = this.peek();
        if (token && token.kind === 'ident' && token.value === value) {
            this.index += 1;
            return true;
        }
        return false;
    }

    private matchOp(value: string): boolean {
        const token = this.peek();
        if (token && token.kind === 'op' && token.value === value) {
            this.index += 1;
            return true;
        }
        return false;
    }

    private parseOr(): void {
        this.parseAnd();
        while (this.matchIdent('or')) {
            this.parseAnd();
        }
    }

    private parseAnd(): void {
        this.parseNot();
        while (this.matchIdent('and')) {
            this.parseNot();
        }
    }

    private parseNot(): void {
        if (this.matchIdent('not')) {
            this.parseNot();
            return;
        }
        this.parseComparison();
    }

    private parseComparison(): void {
        this.parsePrimary();
        const token = this.peek();
        if (token && token.kind === 'op' && ['==', '!=', '>', '>=', '<', '<='].includes(token.value)) {
            this.index += 1;
            this.parsePrimary();
        }
    }

    private parsePrimary(): void {
        const token = this.peek();
        if (!token) {
            throw new ExpressionSyntaxError('Unexpected end of expression');
        }

        if (token.kind === 'op' && token.value === '(') {
            this.index += 1;
            this.parseOr();
            if (!this.matchOp(')')) {
                throw new ExpressionSyntaxError("Missing closing ')'");
            }
            return;
        }

        if (token.kind === 'number' || token.kind === 'string') {
            this.index += 1;
            return;
        }

        if (token.kind === 'ident') {
            if (['true', 'false', 'null'].includes(token.value)) {
                this.index += 1;
                return;
            }
            this.parsePath();
            return;
        }

        throw new ExpressionSyntaxError(`Unexpected token '${token.value}'`);
    }

    private parsePath(): void {
        const first = this.consume();
        if (first.kind !== 'ident') {
            throw new ExpressionSyntaxError('Expected identifier');
        }
        if (first.value !== 'state' && first.value !== 'result') {
            throw new ExpressionSyntaxError("Path root must be 'state' or 'result'");
        }
        while (this.matchOp('.')) {
            const next = this.consume();
            if (next.kind !== 'ident' && next.kind !== 'number') {
                throw new ExpressionSyntaxError("Expected path segment after '.'");
            }
        }
    }
}

function tokenize(expression: string): Token[] {
    const normalized = expression.trim();
    if (!normalized) {
        throw new ExpressionSyntaxError('Expression must not be empty');
    }

    const tokens: Token[] = [];
    TOKEN_PATTERN.lastIndex = 0;
    while (TOKEN_PATTERN.lastIndex < normalized.length) {
        const match = TOKEN_PATTERN.exec(normalized);
        if (!match?.groups) {
            throw new ExpressionSyntaxError(`Unexpected token near position ${TOKEN_PATTERN.lastIndex}`);
        }
        if (match.groups.number) {
            tokens.push({ kind: 'number', value: match.groups.number });
            continue;
        }
        if (match.groups.string) {
            tokens.push({ kind: 'string', value: match.groups.string });
            continue;
        }
        if (match.groups.op) {
            tokens.push({ kind: 'op', value: match.groups.op });
            continue;
        }
        if (match.groups.ident) {
            tokens.push({ kind: 'ident', value: match.groups.ident });
            continue;
        }
        if (match.groups.mismatch) {
            throw new ExpressionSyntaxError(`Unexpected token '${match.groups.mismatch}'`);
        }
    }
    return tokens;
}

export function validateGraphConditionExpression(expression: string): string | null {
    if (!expression.trim()) {
        return null;
    }
    try {
        new Parser(tokenize(expression)).parse();
        return null;
    } catch (error) {
        if (error instanceof Error) {
            return error.message;
        }
        return 'Invalid expression';
    }
}
