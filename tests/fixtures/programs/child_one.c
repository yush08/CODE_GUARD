#include <stdio.h>
#include <stdlib.h>
/* Demo: spawns exactly one child process, then exits — the judge must not
 * leave the child running (job kill-on-close / explicit cleanup). */
int main(void) {
    system("start /b cmd /c ping -n 120 127.0.0.1 >nul");
    printf("child launched\n");
    return 0;
}
