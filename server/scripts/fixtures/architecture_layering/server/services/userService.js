const { PrismaClient } = require('@prisma/client');
const Redis = require('ioredis');

const prisma = new PrismaClient();
const redis = new Redis();

exports.getAll = () => {
  return prisma.user.findMany();
};

exports.cacheWarm = () => {
  return redis.get('users');
};
