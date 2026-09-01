public enum Status {
    ACTIVE, INACTIVE;

    void log() {
    }
}

public record Point(int x, int y) {
    int sum() {
        return x + y;
    }
}
