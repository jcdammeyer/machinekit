#ifndef _RTAPI_SHMKEYS_H
#define _RTAPI_SHMKEYS_H

// the single place for shared memory keys

// convention: allocate a new key such that its
// most significant byte is zero, which is used for 
// instance management. 
// the rtapi_shmem_new code actually ignores the MSB
// and replaces it be the rtapi_instance variable, so we
// dont have to do the big shm key rename right away; just
// make sure the LSB 24bit differ, which is currently the case.
// this convention is also used for the global shm segment.

// actual shm keys are constructed as follows:
#define OS_KEY(key) (( key & 0x00ffffff) | ((rtapi_instance << 24) & 0xff000000))

// formerly emcmotcfg.h
#define DEFAULT_MOTION_SHMEM_KEY 100

// the global segment shm key
#define GLOBAL_KEY  0x08154711     // key for GLOBAL 

// from scope_shm.h
#define SCOPE_SHM_KEY  0x130CF406

// from streamer.h
#define STREAMER_SHMEM_KEY 	0x48535430
#define SAMPLER_SHMEM_KEY	0x48534130

// from hal/classicladder/arrays.c
#define CL_SHMEM_KEY 0x434C522b // "CLR+"

// from hal/hal_priv.h
#define HAL_KEY   0x48414C32	/* key used to open HAL shared memory */

// from rtapi/rtapi_common.h
#define RTAPI_KEY   0x90280A48	/* key used to open RTAPI shared memory */




#endif // _RTAPI_SHMKEYS_H
