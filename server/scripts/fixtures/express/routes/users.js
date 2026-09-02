const { Router } = require('express');

const router = Router();

router.get('/', listUsers);
router.get('/:id', getUser);
router.post('/', createUser);
router.put('/:id', updateUser);
router.delete('/:id', deleteUser);
router.patch('/:id', patchUser);

function listUsers(req, res) {}
function getUser(req, res) {}
function createUser(req, res) {}
function updateUser(req, res) {}
function deleteUser(req, res) {}
function patchUser(req, res) {}

module.exports = router;
