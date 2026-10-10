#include <stdio.h>
/* Demo: writes a file inside its own workspace (observed via
 * workspace-delta file accounting, workspace scope only). */
int main(void) {
    FILE *f = fopen("student_output.txt", "w");
    if (!f) { return 1; }
    fprintf(f, "hello from the student program\n");
    fclose(f);
    printf("file written\n");
    return 0;
}
