#include <stdio.h>
/* Demo: compilation error (missing semicolon). */
int main(void) {
    int x = 5
    printf("%d\n", x);
    return 0;
}
