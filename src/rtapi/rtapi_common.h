#ifndef RTAPI_COMMON_H
#define RTAPI_COMMON_H

/** RTAPI is a library providing a uniform API for several real time
  operating systems.  As of ver 2.0, RTLinux and RTAI are supported.
*/
/********************************************************************
* Description:  rtapi_common.h
*               This file, 'rtapi_common.h', contains typedefs and 
*               other items common to both the realtime and 
*               non-realtime portions of the implementation.
*
* Author: John Kasunich, Paul Corner
* License: LGPL Version 2
*    
* Copyright (c) 2004 All rights reserved.
*
* Last change: 
********************************************************************/

/** This file, 'rtapi_common.h', contains typedefs and other items
    common to both the realtime and non-realtime portions of the
    implementation.  These items are also common to both the RTAI
    and RTLinux implementations, and most likely to any other
    implementations in the Linux environment.  This data is INTERNAL
    to the RTAPI implementation, and should not be included in any
    application modules.
*/

/** Copyright (C) 2003 John Kasunich
                       <jmkasunich AT users DOT sourceforge DOT net>
    Copyright (C) 2003 Paul Corner
                       <paul_c AT users DOT sourceforge DOT net>

  This library is based on version 1.0, which was released into
  the public domain by its author, Fred Proctor.  Thanks Fred!
*/

/* This library is free software; you can redistribute it and/or
  modify it under the terms of version 2.1 of the GNU Lesser General
  Public License as published by the Free Software Foundation.
  This library is distributed in the hope that it will be useful, but
  WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
  Lesser General Public License for more details.

  You should have received a copy of the GNU Lesser General Public
  License along with this library; if not, write to the Free Software
  Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111 USA
*/

/** THE AUTHORS OF THIS LIBRARY ACCEPT ABSOLUTELY NO LIABILITY FOR
  ANY HARM OR LOSS RESULTING FROM ITS USE.  IT IS _EXTREMELY_ UNWISE
  TO RELY ON SOFTWARE ALONE FOR SAFETY.  Any machinery capable of
  harming persons must have provisions for completely removing power
  from all motors, etc, before persons enter any danger area. All
  machinery must be designed to comply with local and national safety
  codes, and the authors of this software can not, and do not, take
  any responsibility for such compliance.
*/

/** This code was written as part of the EMC HAL project.  For more
  information, go to www.linuxcnc.org.
*/

/* Keep the includes here - It might get messy.. */

#ifdef RTAPI
#include <linux/sched.h>	/* for blocking when needed */
#else
#include <sched.h>		/* for blocking when needed */
#endif

#include "rtapi_bitops.h"	/* test_bit() et al. */

#if defined(ULAPI) && defined(BUILD_SYS_USER_DSO)
#include <sys/ipc.h>		/* IPC_* */
#endif

#ifndef NULL
#define NULL 0
#endif


#include THREADS_HEADERS	/* thread-specific headers */


/* module information */
#ifdef MODULE
MODULE_AUTHOR("John Kasunich, Fred Proctor, & Paul Corner");
MODULE_DESCRIPTION("Portable Real Time API");
MODULE_LICENSE("GPL");
#endif


#undef RTAPI_FIFO  // drop support for RTAPI fifos

/* maximum number of various resources */
#define RTAPI_MAX_MODULES	64
#define RTAPI_MAX_TASKS		64
#define RTAPI_MAX_SHMEMS	32

#define DEFAULT_MAX_DELAY	10000

/* random numbers used as signatures */
#define TASK_MAGIC		21979
#define MODULE_MAGIC		30812

#define MIN_STACKSIZE		32768

/* This file contains data structures that live in shared memory and
   are accessed by multiple different programs, both user processes
   and kernel modules.  If the structure layouts used by various
   programs don't match, that's bad.  So we have revision checking.
   Whenever a module or program is loaded, thread_flavor_id and
   serial is checked against the code in the shared memory area.  If
   they don't match, the rtapi_init() call will fail.
  */

/* These structs hold data associated with objects like tasks, etc. */

typedef enum {
    NO_MODULE = 0,
    REALTIME,
    USERSPACE
} mod_type_t;

typedef struct {
    mod_type_t state;
    char name[RTAPI_NAME_LEN + 1];
#ifdef THREAD_MODULE_DATA
    THREAD_MODULE_DATA;
#endif
} module_data;

typedef enum {
    EMPTY = 0,
    PAUSED,
    PERIODIC,
    FREERUN,
    ENDED,
    USERLAND,
    DELETE_LOCKED	// task ready to be deleted; mutex already obtained
} task_state_t;

typedef struct {
#if defined(RTAPI_XENOMAI_USER)		/* hopefully this can be removed
					   somehow */
    char name[XNOBJECT_NAME_LEN];
#else
    char name[RTAPI_NAME_LEN];
#endif
    int magic;
    int uses_fp;
    size_t stacksize;
    int period;
    int ratio;
    task_state_t state;		/* task state */
    int prio;			/* priority */
    int owner;			/* owning module */
    void (*taskcode) (void *);	/* task code */
    void *arg;			/* task argument */
    int cpu;
#ifdef THREAD_TASK_DATA
    THREAD_TASK_DATA;		/* task data defined in thread system */
#endif
} task_data;

typedef struct {
    int magic;			/* to check for valid handle */
    int key;			/* key to shared memory area */
    int id;			/* OS identifier for shmem */
    int count;                  /* count of maps in this process */
    int rtusers;		/* number of realtime modules using block */
    int ulusers;		/* number of user processes using block */
    unsigned long size;		/* size of shared memory area */
    _DECLARE_BITMAP(bitmap, RTAPI_MAX_SHMEMS+1);
				/* which modules are using block */
    void *mem;			/* pointer to the memory */
} shmem_data;

/* Master RTAPI data structure
   There is a single instance of this structure in the machine.
   It resides in shared memory, where it can be accessed by both
   realtime (RTAPI) and non-realtime (ULAPI) code.  It contains
   all information about the current state of RTAPI/ULAPI and
   the associated resources (tasks, etc.).
*/

typedef struct {
    int magic;			/* magic number to validate data */
    int serial;			/* revision code for matching */
    int thread_flavor_id;	/* unique ID for each thread style: rtapi.h */
    unsigned long mutex;	/* mutex against simultaneous access */
    int rt_module_count;	/* loaded RT modules */
    int ul_module_count;	/* running UL processes */
    int task_count;		/* task IDs in use */
    int shmem_count;		/* shared memory blocks in use */
    int timer_running;		/* state of HW timer */
    int rt_cpu;			/* CPU to use for RT tasks */
    long int timer_period;	/* HW timer period */
    module_data module_array[RTAPI_MAX_MODULES + 1];	/* data for modules */
    task_data task_array[RTAPI_MAX_TASKS + 1];	/* data for tasks */
    shmem_data shmem_array[RTAPI_MAX_SHMEMS + 1];	/* data for shared
							   memory */
#ifdef THREAD_RTAPI_DATA
    THREAD_RTAPI_DATA;		/* RTAPI data defined in thread system */
#endif
} rtapi_data_t;

// this segment is unversally shared between ULAPI and RTAPI, kernel or userland
// and can be used for any system-wide data which needs to be universally
// accessible

typedef struct {
    int magic;
    int layout_version; 
    unsigned long mutex;
    int rtapi_thread_flavor; 
    int msg_level;                 // a single global message level
    int next_module_id;           // for userland threads module id's
} global_data_t;

#define GLOBAL_KEY  0x08154711     // key for GLOBAL 
#define GLOBAL_LAYOUT_VERSION 42   // bump on layout changes of global_data_t
#define GLOBAL_MAGIC 0xdeadbeef
#define GLOBAL_DATA_PERMISSIONS	0666

/* rtapi_common.c */
extern rtapi_data_t *rtapi_data;

// inited at RTAPI/ULAPI init time, and exported like rtapi_data
extern  global_data_t *global_data;

#if defined(RTAPI) 
extern void init_rtapi_data(rtapi_data_t * data);
extern void init_global_data(global_data_t * data);
#endif

#if defined(ULAPI) && defined(BUILD_SYS_USER_DSO)
extern int global_data_attach(key_t key, global_data_t **global_data);
#endif

#if defined(RTAPI) && defined(BUILD_SYS_USER_DSO)
extern int  next_module_id(void);
#endif

/* rtapi_task.c */
extern task_data *task_array;


/* $(THREADS).c */
#if defined(MODULE)
extern RT_TASK *ostask_array[];
#endif

/* rtapi_time.c */
#ifdef BUILD_SYS_USER_DSO
extern int period;
#else /* BUILD_SYS_KBUILD */
extern long int max_delay;
extern unsigned long timer_counts;
#endif
#ifdef HAVE_RTAPI_MODULE_TIMER_STOP
void rtapi_module_timer_stop(void);
#endif


/* rtapi_shmem.c */
#define RTAPI_KEY   0x90280A48	/* key used to open RTAPI shared memory */
#define RTAPI_MAGIC 0x12601409	/* magic number used to verify shmem */
#define SHMEM_MAGIC_DEL_LOCKED 25454  /* don't obtain mutex when deleting */

extern shmem_data *shmem_array;
extern void *shmem_addr_array[];


/* rtapi_module.c */
#ifndef BUILD_SYS_USER_DSO
extern int init_master_shared_memory(rtapi_data_t **rtapi_data);
#endif
extern module_data *module_array;

#endif /* RTAPI_COMMON_H */
