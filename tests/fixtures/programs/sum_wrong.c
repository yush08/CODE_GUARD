#include <stdio.h>
/* Demo: wrong answer. Prints a - b instead of a + b. */
int main(void) {
    long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) { return 1; }
    printf("%lld\n", a - b);
    return 0;
}
