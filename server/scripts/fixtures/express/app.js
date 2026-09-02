const express = require('express');
const usersRouter = require('./routes/users');

const app = express();

app.get('/health', healthCheck);
app.post('/echo', (req, res) => {
  res.json(req.body);
});
app.use('/api/users', usersRouter);

function healthCheck(req, res) {
  res.send('ok');
}

module.exports = app;
