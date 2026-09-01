public interface Callback {
    void onDone();

    default void onStart() {
    }
}

abstract class Base {
    abstract void doThing();

    void concrete() {
    }
}
