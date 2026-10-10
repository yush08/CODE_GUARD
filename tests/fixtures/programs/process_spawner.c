#include <stdio.h>
#include <stdlib.h>
/* Demo: process-limit scenario. Launches several long-lived child processes
 * concurrently (start /b returns immediately) until the configured
 * active-process policy is exceeded. The judge terminates the tree. */
int main(void) {
    int i;
    for (i = 0; i < 6; i++) {
        int rc = system("start /b cmd /c ping -n 120 127.0.0.1 >nul");
        if (rc != 0) {
            printf("spawn attempt %d failed (rc=%d)\n", i, rc);
        }
    }
    printf("spawn attempts finished\n");
    fflush(stdout);
    /* keep parent alive so children stay active */
    for (;;) { }
    return 0;
}
