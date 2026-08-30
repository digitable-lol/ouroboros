/* Свёртка выражения слева направо: "12 + 5 * 3 - 4". */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int is_op(const char *s) {
    return strlen(s) == 1 && (s[0] == '+' || s[0] == '-' || s[0] == '*');
}

int to_int(const char *s) {
    return (int)strtol(s, NULL, 10);
}

int apply(int acc, char op, int value) {
    if (op == '+') return acc + value;
    if (op == '-') return acc - value;
    if (op == '*') return acc * value;
    return acc;
}

int complain(const char *what) {
    printf("непонятное слово %s\n", what);
    return 0;
}

int evaluate(int count, char **words) {
    int acc = to_int(words[0]);
    char op = '+';
    for (int i = 1; i < count; i++) {
        if (is_op(words[i])) {
            op = words[i][0];
            continue;
        }
        int value = to_int(words[i]);
        acc = apply(acc, op, value);
    }
    return acc;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        return complain("пусто");
    }
    printf("итог %d\n", evaluate(argc - 1, argv + 1));
    return 0;
}
