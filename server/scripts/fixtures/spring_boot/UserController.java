package com.example.demo;

@RestController
@RequestMapping("/api/users")
public class UserController {

    @GetMapping("/{id}")
    public User getUser(@PathVariable Long id) {
        return null;
    }

    @GetMapping
    public List<User> listUsers() {
        return null;
    }

    @PostMapping("/")
    public User createUser(@RequestBody User user) {
        return null;
    }

    @PutMapping("/{id}")
    public User updateUser(@PathVariable Long id, @RequestBody User user) {
        return null;
    }

    @DeleteMapping("/{id}")
    public void deleteUser(@PathVariable Long id) {
    }

    @RequestMapping(value = "/{id}/archive", method = RequestMethod.PATCH)
    public User archiveUser(@PathVariable Long id) {
        return null;
    }

    public String notARoute() {
        return "no annotation, not extracted";
    }
}
