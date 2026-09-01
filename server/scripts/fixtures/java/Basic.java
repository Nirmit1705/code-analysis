package com.example;

/** A greeter class. */
public class Greeter {
    private String name;

    /** Constructs a greeter. */
    public Greeter(String name) {
        this.name = name;
    }

    /** Says hi. */
    public String greet() {
        return "Hi " + name;
    }

    public static Greeter create() {
        return new Greeter("x");
    }
}
