package com.example.demo;

public class PlainService {
    @GetMapping("/ignored")
    public String notAController() {
        // Not annotated @RestController/@Controller — even though this
        // method carries a mapping annotation, the class itself doesn't
        // qualify, so nothing here should be extracted as a route.
        return "ignored";
    }
}

@Controller
public class PageController {

    @GetMapping("/home")
    public String home() {
        return "home";
    }
}
