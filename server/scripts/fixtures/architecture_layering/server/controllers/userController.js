const userService = require('../services/userService');

exports.list = (req, res) => {
  res.json(userService.getAll());
};
