package com.example.app;

import com.example.app.service.UserService;
import static com.example.app.util.Constants.MAX;
import com.example.app.model.*;
import java.util.List;

public class Controller {
    private final UserService service = new UserService();

    public int max() {
        return MAX;
    }
}
